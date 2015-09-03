#!/usr/bin/env python
# coding=utf-8
from __future__ import division, print_function, unicode_literals
from datetime import datetime
import math
import numpy as np
import sys
from brainstorm.randomness import Seedable
from brainstorm.utils import IteratorValidationError


def progress_bar(maximum, prefix='[',
                 bar='====1====2====3====4====5====6====7====8====9====0',
                 suffix='] Took: {0}\n'):
    i = 0
    start_time = datetime.utcnow()
    out = prefix
    while i < len(bar):
        progress = yield out
        j = math.trunc(progress / maximum * len(bar))
        out = bar[i: j]
        i = j
    elapsed_str = str(datetime.utcnow() - start_time)[: -5]
    yield out + suffix.format(elapsed_str)


def silence():
    while True:
        _ = yield ''


class DataIterator(object):
    def __call__(self, handler, verbose=False):
        pass


class Undivided(DataIterator):
    """
    Processes the data in one block (only one iteration).
    """
    def __init__(self, **named_data):
        """
        :param named_data: named arrays with 3+ dimensions ('T', 'B', ...)
        :type named_data: dict[str, ndarray]
        """
        _ = _assert_correct_data_format(named_data)
        self.data = named_data
        self.total_size = sum(d.size for d in self.data.values())

    def __call__(self, handler, verbose=False):
        if isinstance(self.data, handler.array_type):
            yield self.data
        else:
            arr = handler.allocate(self.total_size)
            device_data = {}
            i = 0
            for key, value in self.data.items():
                device_data[key] = arr[i: i + value.size].reshape(value.shape)
                handler.set_from_numpy(device_data[key], value)
                i += value.size

        yield device_data


class Online(DataIterator, Seedable):
    """
    Online (one sample at a time) iterator for inputs and targets.
    """
    def __init__(self, shuffle=True, verbose=None, seed=None, **named_data):
        Seedable.__init__(self, seed=seed)
        self.nr_sequences = _assert_correct_data_format(named_data)
        self.data = named_data
        self.shuffle = shuffle
        self.verbose = verbose
        self.sample_size = sum(d.shape[0] * np.prod(d.shape[2:])
                               for d in self.data.values())

    def __call__(self, handler, verbose=False):
        if (self.verbose is None and verbose) or self.verbose:
            p_bar = progress_bar(self.nr_sequences)
        else:
            p_bar = silence()

        need_copy = not all([isinstance(v, handler.array_type)
                            for v in self.data.values()])
        if need_copy:
            arr = handler.allocate(self.sample_size)

        print(next(p_bar), end='')
        sys.stdout.flush()
        indices = np.arange(self.nr_sequences)
        if self.shuffle:
            self.rnd.shuffle(indices)
        for i, idx in enumerate(indices):
            if need_copy:
                device_data = {}
                j = 0
                for key, value in self.data.items():
                    val_s = value[:, idx: idx + 1]
                    device_data[key] = arr[j: j + val_s.size].reshape(
                        val_s.shape)
                    handler.set_from_numpy(device_data[key], val_s)
                    j += val_s.size
            else:
                device_data = {k: v[:, idx: idx + 1]
                               for k, v in self.data.items()}
            yield device_data
            print(p_bar.send(i + 1), end='')
            sys.stdout.flush()


class Minibatches(DataIterator, Seedable):
    """
    Minibatch iterator for inputs and targets.

    Only randomizes the order of minibatches, doesn't shuffle between
    minibatches.
    """
    def __init__(self, batch_size=10, shuffle=True, verbose=None,
                 seed=None, **named_data):
        Seedable.__init__(self, seed=seed)
        self.nr_sequences = _assert_correct_data_format(named_data)
        self.data = named_data
        self.shuffle = shuffle
        self.verbose = verbose
        self.batch_size = batch_size
        self.sample_size = sum(d.shape[0] * np.prod(d.shape[2:]) * batch_size
                               for d in self.data.values())

    def __call__(self, handler, verbose=False):
        if (self.verbose is None and verbose) or self.verbose:
            p_bar = progress_bar(self.nr_sequences)
        else:
            p_bar = silence()

        need_copy = not all([isinstance(v, handler.array_type)
                            for v in self.data.values()])
        if need_copy:
            arr = handler.allocate(self.sample_size)

        print(next(p_bar), end='')
        sys.stdout.flush()
        indices = np.arange(int(math.ceil(self.nr_sequences / self.batch_size)))
        if self.shuffle:
            self.rnd.shuffle(indices)
        for i, idx in enumerate(indices):
            chunk = (slice(None),
                     slice(idx * self.batch_size, (idx + 1) * self.batch_size))

            if need_copy:
                device_data = {}
                j = 0
                for key, value in self.data.items():
                    val_s = value[chunk]
                    device_data[key] = arr[j: j+val_s.size].reshape(
                        val_s.shape)
                    handler.set_from_numpy(device_data[key], val_s)
                    j += val_s.size
            else:
                device_data = {k: v[chunk]
                               for k, v in self.data.items()}
            yield device_data
            print(p_bar.send((i + 1) * self.batch_size), end='')
            sys.stdout.flush()


def _assert_correct_data_format(named_data):
    nr_sequences = {}
    for name, data in named_data.items():
        if not hasattr(data, 'shape'):
            raise IteratorValidationError(
                "{} has a wrong type. (no shape attribute)".format(name)
            )
        if len(data.shape) < 3:
            raise IteratorValidationError(
                'All inputs have to have at least 3 dimensions, where the '
                'first two are time_size and batch_size.')
        nr_sequences[name] = data.shape[1]

    if min(nr_sequences.values()) != max(nr_sequences.values()):
        raise IteratorValidationError(
            'The number of sequences of all inputs must be equal, but got {}'
            .format(nr_sequences))

    return min(nr_sequences.values())
