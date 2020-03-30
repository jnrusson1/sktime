import numpy as np
import pandas as pd

import numbers
from collections.abc import Iterable
from typing import (
    Any,
    Callable,
    Optional,
    Sequence,
    Tuple,
    Type
)

from pandas.api.extensions import (
    ExtensionArray,
    ExtensionDtype,
    register_extension_dtype
)

from sktime.container import TimeBase
from sktime.container.base import check_data_index, ensure_2d


# -----------------------------------------------------------------------------
# Extension Type
# -----------------------------------------------------------------------------
class TimeDtype(ExtensionDtype):
    type = TimeBase
    name = "timeseries"
    kind = 'O'
    na_value = None

    @classmethod
    def construct_from_string(cls, string):
        if string == cls.name:
            return cls()
        else:
            raise TypeError("Cannot construct a 'TimeDtype' from '{}'".format(string))

    @classmethod
    def construct_array_type(cls):
        return TimeArray

# Register the datatype with pandas
register_extension_dtype(TimeDtype)


# -----------------------------------------------------------------------------
# Constructors / converters to other formats
# -----------------------------------------------------------------------------

def from_list(data):
    n = len(data)

    out_d = []
    out_i = []
    widths = np.full((n, ), np.nan, dtype=np.float)

    for idx in range(n):
        ts = data[idx]

        # TODO: think about other constructors that might make sense
        if isinstance(ts, (TimeBase, TimeArray)):
            check_data_index(ts.data, ts.time_index)
            out_d.append(ts.data)
            out_i.append(ts.time_index)
            widths[idx] = ts.data.shape[1]
        elif isinstance(ts, (tuple, list, np.ndarray)):
            out_d.append(ensure_2d(np.array(ts[0])))
            out_i.append(ensure_2d(np.array(ts[1])))
            widths[idx] = out_d[idx].shape[1]
        elif isinstance(ts, pd.Series):
            out_d.append(ts.values)
            out_i.append(ts.index.values)
            widths[idx] = ts.values.size
        elif ts is None:
            out_d.append(None)
            out_i.append(None)
        else:
            raise TypeError("Input must be valid timeseries objects: {0}".format(ts))

    # Deal with missing
    mask = np.isnan(widths)
    if np.all(mask):
        #if n <= 1:
        #    return None
        #return np.array([None for _ in range(n)])
        return TimeArray(np.full((n, 0), np.nan, dtype=np.float))[:n]
    elif np.any(mask):
        # TODO: is there a better way to do this?
        w = out_d[int(np.where(~mask)[0][0])].shape[1]
        out_d = [np.full((1, w), np.nan, np.float) if m else d for m, d in zip(mask, out_d)]
        out_i = [np.full((1, w), np.nan, np.float) if m else i for m, i in zip(mask, out_i)]

    # Ensure that all the widths are equal
    if not np.all(widths[~mask] == widths[~mask][0]):
        raise ValueError("The width of each timeseries must be the equal.")

    return TimeArray(np.vstack(out_d), np.vstack(out_i))


def from_pandas(data):
    if isinstance(data, pd.Series):
        return from_list(data)
    elif isinstance(data, pd.DataFrame):
        time_index = data.columns.values[np.newaxis, :]
        time_index = np.repeat(time_index, data.shape[0], axis=0)
        return TimeArray(data.to_numpy(), time_index)
    else:
        raise TypeError("Input must be a pandas Series or DataFrame: {0}".format(data))


def from_ts(ts):
    # Note: this is a very(!) preliminary implementation to convert to ts format, used solely
    #       to allow for factorization and pass pandas implementation tests. This will
    #       be extended in the future, after TimeArray compatibility with pandas has been
    #       resolved.
    # TODO: transfer a full implementation from sktime.utils.load_data.load_from_tsfile_to_dataframe
    def parse_line(line):
        line = line.strip("()")
        points = line.split("),(")
        index, data = zip(*[x.split(",") for x in points])
        return np.array(data, dtype=np.float), np.array(index, dtype=np.float)

    return from_list([TimeBase(*parse_line(l)) for l in ts])

def to_ts(obj, include_header=True):
    # Note: this is a very preliminary implementation to convert to ts format. This will
    #       be extended in the future, after TimeArray compatibility with pandas has been
    #       resolved.
    # TODO: extend to cover all cases
    if include_header:
        raise NotImplementedError("Conversion to ts format with headers not supported yet.")

    if not isinstance(obj, (TimeBase, TimeArray)):
        raise TypeError(f"Can only convert TimeBase or TimeArray objects, got {type(obj)}")
    elif isinstance(obj, TimeArray):
        obj = obj.astype(object)
    else:
        obj = [obj]

    # TODO: this returns a list which is needed for factorise but we might want to move this
    #       into a separate function and return a string here
    return ["(" + "),(".join([str(i) + "," + str(d) for i, d in zip(x.data[0], x.time_index[0])]) + ")" for x in obj]




# ----------------------------------------------------------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------------------------------------------------------

def empty(shape, dtype=np.float):
    return np.full(shape, np.nan, dtype=dtype)


# ----------------------------------------------------------------------------------------------------------------------
# Extension Container
# ----------------------------------------------------------------------------------------------------------------------

# TODO: update docstrings to reflect recent refactoring

class TimeArray(ExtensionArray):
    """Holder for multiple timeseries objects with an equal number of observations.

    TimeArray is a container for storing and manipulating timeseries data. Each
    observation in TimeArray is a distinct timeseries, i.e. a series of key/value
    pairs that represent a time point and a corresponding measurement.

    A note on the internal data layout: TimeArray implements a
    collection of equal length timeseries in which rows correspond to
    a single observed timeseries. We use a Numpy array to store the
    individual data points of each time series, i.e. columns relate to
    specific points in time. The time indices are stored in a separate
    Numpy array.

    Note on compatibility with pandas:
    TimeArray aims to to satisfy pandas' extension array interface so it can be
    stored inside :class:`pandas.Series` and :class:`pandas.DataFrame`. While this
    works for basic indexing already, many of the more intricate functionality of
    pandas (e.g. apply or groupby) has not been integrated yet.

    Parameters
    ----------
    data : TimeArray or ndarray
        The measurements at certain time points (columns) for one or more
        timeseries (rows).
    time_index : ndarray, optional
        A time index for each entry in 'data'. Must be of the same shape as
        data. If None, the time index stored in 'data' will be used if 'data'
        is a TimeArray object, or a default time index of [0, 1, ..., N] will be
        generated for each row if 'data' is a ndarray.
    copy : bool, default False
        If True, copy the underlying data.
    """

    _dtype = TimeDtype()
    _can_hold_na = True

    @property
    def _constructor(self) -> Type["TimeArray"]:
        return TimeArray

    def __init__(self, data, time_index=None, copy=False):
        tidx = None

        # Initialise the data
        if data is None:
            # TODO: what if data is None by index is passed?
            data = np.full((1,0), np.nan, dtype=np.float)
        elif isinstance(data, self.__class__):
            tidx = data.time_index
            data = data.data
        elif not isinstance(data, np.ndarray):
            raise TypeError(f"'data' should be TimeArray or numpy.ndarray, got {type(data)}. "
                            f"Use from_list, to construct a TimeArray from list-like objects.")
        elif not data.ndim == 2:
            raise ValueError("'data' should be a 2-dimensional, where rows correspond"
                             "to timeseries and columns to individual observations.")

        # Initialise the time index
        if time_index is not None:
            if not isinstance(time_index, np.ndarray):
                raise TypeError(f"'time_index' should be numpy.ndarray, got {type(time_index)}.")
            tidx = time_index
        elif tidx is None:
            if data.shape[0] == 1:
                tidx = np.arange(data.shape[1])[np.newaxis, :]
            elif data.shape[0] > 1:
                tidx = np.vstack([np.arange(data.shape[1]) for _ in range(data.shape[0])])
            else:
                tidx = data.copy()

        if copy:
            data = data.copy()
            tidx = tidx.copy()

        self.data = data
        self.time_index = tidx
        check_data_index(self.data, self.time_index)

    # -------------------------------------------------------------------------
    # Class methods
    # -------------------------------------------------------------------------

    @classmethod
    def _from_sequence(cls, scalars, dtype=None, copy=False) -> Type[None]:
        """
        Construct a new TimeArray from a sequence of scalars.

        Parameters
        ----------
        scalars : Sequence
            Each element should be a timeseries-like object,
            see from_list() for a detailed description.
        dtype : dtype, optional
            Construct for this particular dtype. This is currently ignored
            and only included for compatibility
        copy : bool, default False
            If True, copy the underlying data.

        Returns
        -------
        TimeArray
        """

        if isinstance(scalars, TimeArray):
            return scalars
        return from_list(scalars)

    def _values_for_factorize(self) -> Tuple[np.ndarray, Any]:
        """Return an array and missing value suitable for factorization.

        Note: strings following the ts file format are used to make
              TimeArrays suitable for factorization

        Returns
        -------
        values : ndarray
            An array of type String suitable for factorization.
        na_value : object
            The value in `values` to consider missing. Not implemented
            yet.
        """
        # TODO: also consider na_value
        vals = to_ts(self, False)
        return vals, None

    @classmethod
    def _from_factorized(cls, values, original):
        """
        Reconstruct a TimeArray after factorization.

        Parameters
        ----------
        values : ndarray
            An integer ndarray with the factorized values.
        original : TimeArray
            The original TimeArray that factorize was called on.

        See Also
        --------
        factorize
        ExtensionArray.factorize
        """
        # TODO: look into how the original array could be better utilised to avoid duplication
        return from_ts(values)

    @classmethod
    def _concat_same_type(cls, to_concat):
        if not np.all([isinstance(x, TimeArray) for x in to_concat]):
            # TODO: should we also allow to concatenate a np.array of None (i.e. missing)
            raise TypeError("Only TimeArrays can be concatenated.")

        widths = np.array([x.data.shape[1] for x in to_concat])
        ref_width = np.unique(widths[widths > 0])

        if len(ref_width) == 0:
            ref_width = 0
        elif len(ref_width) == 1:
            ref_width = ref_width[0]
        else:
            raise ValueError("Lengths of concatenated TimeArrays must be equal.")

        data = [empty((x.shape[0], ref_width)) if w == 0 else x.data for w, x in zip(widths, to_concat)]
        tidx = [empty((x.shape[0], ref_width)) if w == 0 else x.time_index for w, x in zip(widths, to_concat)]

        return TimeArray(np.vstack(data), np.vstack(tidx))

    # -------------------------------------------------------------------------
    # Interfaces
    # -------------------------------------------------------------------------

    def __getitem__(self, idx):
        # validate and convert IntegerArray / BooleanArray
        # keys to numpy array, pass-through non-array-like indexers
        idx = pd.api.indexers.check_array_indexer(self, idx)

        if isinstance(idx, (int, np.int, numbers.Integral)):
            if np.all(np.isnan(self.data[idx])) and np.all(np.isnan(self.time_index[idx])):
                # Return the missing type if both data and time_index are completely missing
                return None

            return TimeBase(self.data[idx], self.time_index[idx])
        elif isinstance(idx, (Iterable, slice)):
            return self._constructor(self.data[idx], self.time_index[idx])
        else:
            raise TypeError("Index type not supported", idx)


    def __setitem__(self, key, value):
        # validate and convert IntegerArray / BooleanArray
        # keys to numpy array, pass-through non-array-like indexers
        key = pd.api.indexers.check_array_indexer(self, key)

        if value is not None and not isinstance(value, (TimeBase, TimeArray)):
            value = from_list(value)

        # TODO: add dimensionality check

        if value is None or np.all(value.isna()):
            # This is setting all `key` elements to missing
            self.data[key] = np.full((1, ), np.nan, dtype=np.float)
            self.time_index[key] = np.full((1, ), np.nan, dtype=np.float)
        else:
            self.data[key] = value.data
            self.time_index[key] = value.time_index

    @property
    def dtype(self):
        return self._dtype

    @property
    def size(self):
        return self.data.size

    @property
    def shape(self):
        return (self.data.shape[0], )

    @property
    def ndim(self):
        return 1

    @property
    def nbytes(self):
        return self.data.nbytes


    # ------------------------------------------------------------------------
    # Ops
    # ------------------------------------------------------------------------

    def isna(self):
        # TODO: revisit missingness
        return self.isna_row(self.data) & self.isna_row(self.time_index)

    def isna_row(self, arr):
        return np.apply_over_axes(np.all, self.isna_grid(arr), 1).flatten()

    def isna_grid(self, arr):
        # TODO: revisit missingness
        return np.isnan(arr)

    def astype(self, dtype, copy=True):
        if isinstance(dtype, TimeDtype):
            if copy:
                return self.copy()
            else:
                return self
        elif dtype is str or (hasattr(dtype, 'kind') and dtype.kind in {'U', 'S'}):
            return np.array(self).astype(dtype)
        elif dtype is object or (hasattr(dtype, 'kind') and dtype.kind in {'O'}):
            return np.array(self)
        raise ValueError(f"TimeArray can only be cast to numpy array with type object",
                         f"got type {dtype}")

    def unique(self):
        from pandas import factorize

        rows, _ = factorize(np.array(self).astype(str))

        return self[np.unique(rows)]

    # -------------------------------------------------------------------------
    # general array like compat
    # -------------------------------------------------------------------------

    def __len__(self):
        return self.shape[0]

    def __array__(self, dtype=None):
        return np.array([TimeBase(self.data[i], self.time_index[i]) for i in range(self.data.shape[0])])

    def __array_ufunc__(self, ufunc: Callable, method: str, *inputs: Any, **kwargs: Any):
        raise NotImplementedError("__array_ufunc__ not implemented yet")

    def __eq__(self, other):
        if isinstance(other, pd.Series):
            comp_data = np.broadcast_to(other.values[np.newaxis, :], self.data.shape)
            comp_index = np.broadcast_to(other.index.values[np.newaxis, :], self.time_index.shape)
        else:
            comp_data = other.data
            comp_index = other.time_index
        # TODO: sense check for other types

        return (np.all(self.data == comp_data)) & (np.all(self.time_index == comp_index))

    def __add__(self, o):
        if np.all(self.time_index != o.time_index):
            raise ValueError("The time indices of two TimeArrays that should be added must be identical.")

        return TimeArray(self.data + o.data, time_index=self.time_index)

    def copy(self, *args, **kwargs):
        return TimeArray(self.data.copy(), self.time_index.copy())

    def take(self, indices, allow_fill=False, fill_value=None):
        # Use the take implementation from pandas to get the takes separately
        # for the data and the time indices
        from pandas.api.extensions import take
        data = take(self.data, indices, allow_fill=allow_fill)
        time_index = take(self.time_index, indices, allow_fill=allow_fill)

        if allow_fill and isinstance(fill_value, TimeBase):
            # Fill those indices that were out of range with the fill TimeBase
            indices = np.asarray(indices, dtype=np.intp)
            out_of_range = (indices < 0) | (indices >= data.shape[0])

            data[self.isna_row(data) & out_of_range] = fill_value.data
            time_index[self.isna_row(time_index) & out_of_range] = fill_value.time_index
        elif allow_fill and fill_value is not None:
            TypeError(f"Only TimeBase are allowed as fill values, got {type(fill_value)}")

        if data.shape[0] != 0 and np.all(np.isnan(data)):
            # If the take resulted in a missing TimeArray (note, this is
            # different from a length 0 TimeArray)
            # TODO: put in a separate function if needed elsewhere
            return TimeArray(np.full((data.shape[0], 0), np.nan, dtype=np.float))

        return self._constructor(data, time_index)

    # ------------------------------------------------------------------------
    # Printing
    # ------------------------------------------------------------------------

    def __repr__(self) -> str:
        from pandas.io.formats.printing import format_object_summary

        template = "{class_name}{data}\nLength: {length}, dtype: {dtype}"
        data = format_object_summary(
            self, self._formatter(), indent_for_name=False, is_justify=False
        ).rstrip(", \n")
        class_name = "<{}>\n".format(self.__class__.__name__)

        return template.format(
            class_name=class_name, data=data, length=len(self), dtype=self.dtype
        )

    def _formatter(self, boxed: bool = False) -> Callable[[Any], Optional[str]]:
        if boxed:
            return str
        return repr


    # -------------------------------------------------------------------------
    # time series functionality
    # -------------------------------------------------------------------------

    def tabularise(self, name=None, return_array=False):
        if name is None:
            name = "dim"
        if return_array:
            return self.data
        # TODO: throw a naming error when time indes isn't equal in each row
        return pd.DataFrame(self.data, columns=[name + "_" + str(i) for i in self.time_index[0]])

    def tabularize(self, return_array=False):
        return self.tabularise(return_array)

    def check_equal_index(self):
        if self.time_index.ndim == 1 or self.time_index.shape[0] == 0:
            # TODO: change if one time index isn't allowed anymore
            self._equal_index = True
        else:
            self._equal_index = (self.time_index == self.time_index[0]).all()

        return self._equal_index

    def slice_time(self, time_index):
        # TODO: this silently assumes that time indices are the same. Decide how this
        #       function should work if they are not (and the result can be a ragged/
        #       non-equal length array)
        sel = np.isin(self.time_index[0, :], time_index)
        return TimeArray(self.data[:, sel], time_index=self.time_index[:, sel])