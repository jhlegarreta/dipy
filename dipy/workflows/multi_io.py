from glob import glob
import inspect
import itertools
import os
from pathlib import Path
import re

import numpy as np

from dipy.testing.decorators import warning_for_keywords
from dipy.workflows.base import get_args_default


def _resolve_path_or_pattern(path):
    """Resolve a file path, directory, or glob pattern into a list of Path objects.

    Parameters
    ----------
    path : str or Path
        A single path string or Path object that can be a file, directory, or
        glob pattern.

    Returns
    -------
    list of paths
        A sorted list of matching paths. Empty if no matches found.
    """

    if re.search(r"[*?\[\]]", str(path)):
        return sorted(Path(p) for p in glob(str(path)))
    else:
        return (
            sorted([path])
            if Path(path).is_file()
            else sorted(Path(path).glob("*"))
            if Path(path).is_dir()
            else []
        )


def common_start(sa, sb):
    """Return the longest common substring from the beginning of sa and sb."""

    def _iter():
        for a, b in zip(sa, sb):
            if a == b:
                yield a
            else:
                return

    return "".join(_iter())


def slash_to_under(dir_str):
    return "".join(dir_str.replace("/", "_"))


@warning_for_keywords()
def connect_output_paths(
    inputs, out_dir, out_files, *, output_strategy="absolute", mix_names=True
):
    """Generate a list of output files paths based on input files and
    output strategies.

    Parameters
    ----------
    inputs : array
        List of input paths.
    out_dir : string or Path
        The output directory.
    out_files : array
        List of output files.
    output_strategy : string, optional
        Which strategy to use to generate the output paths.
            'append': Add out_dir to the path of the input.
            'prepend': Add the input path directory tree to out_dir.
            'absolute': Put directly in out_dir.
    mix_names : bool, optional
        Whether or not prepend a string composed of a mix of the input
        names to the final output name.

    Returns
    -------
        A list of output file paths.

    """
    outputs = []
    if isinstance(inputs, (str, Path)):
        inputs = [inputs]
    if isinstance(out_files, (str, Path)):
        out_files = [out_files]

    sizes_of_inputs = [len(inp) for inp in inputs]

    max_size = np.max(sizes_of_inputs)
    min_size = np.min(sizes_of_inputs)
    if min_size > 1 and min_size != max_size:
        raise ImportError("Size of input issue")

    elif min_size == 1:
        for i, sz in enumerate(sizes_of_inputs):
            if sz == min_size:
                inputs[i] = max_size * inputs[i]

    if mix_names:
        mixing_prefixes = concatenate_inputs(inputs)
    else:
        mixing_prefixes = [""] * len(inputs[0])

    for mix_pref, inp in zip(mixing_prefixes, inputs[0]):
        inp_dirname = Path(inp).parent
        if output_strategy == "prepend":
            if Path(out_dir).is_absolute():
                dname = Path(out_dir) / inp_dirname
            if not Path(out_dir).is_absolute():
                dname = Path(os.getcwd()) / out_dir / inp_dirname

        elif output_strategy == "append":
            dname = Path(inp_dirname) / out_dir

        else:
            dname = out_dir

        updated_out_files = []
        for out_file in out_files:
            updated_out_files.append(Path(dname) / (mix_pref + str(out_file)))

        outputs.append(updated_out_files)

    return inputs, outputs


def concatenate_inputs(multi_inputs):
    """Concatenate list of inputs."""
    mixing_names = []
    for inps in zip(*multi_inputs):
        mixing_name = ""
        for inp in inps:
            inp = Path(inp)
            mixing_name += inp.name.removesuffix("".join(inp.suffixes)) + "_"

        mixing_names.append(mixing_name + "_")
    return mixing_names


@warning_for_keywords()
def io_iterator(
    inputs,
    out_dir,
    fnames,
    *,
    output_strategy="absolute",
    mix_names=False,
    out_keys=None,
):
    """Create an IOIterator from the parameters.

    Parameters
    ----------
    inputs : array
        List of input files.
    out_dir : string
        Output directory.
    fnames : array
        File names of all outputs to be created.
    output_strategy : string, optional
        Controls the behavior of the IOIterator for output paths.
    mix_names : bool, optional
        Whether or not to append a mix of input names at the beginning.
    out_keys : list, optional
        Output parameter names.

    Returns
    -------
        Properly instantiated IOIterator object.

    """
    io_it = IOIterator(output_strategy=output_strategy, mix_names=mix_names)
    io_it.set_inputs(*inputs)
    io_it.set_out_dir(out_dir)
    io_it.set_out_fnames(*fnames)
    io_it.create_outputs()
    if out_keys:
        io_it.set_output_keys(*out_keys)

    return io_it


@warning_for_keywords()
def _io_iterator(frame, fnc, *, output_strategy="absolute", mix_names=False):
    """Create an IOIterator using introspection.

    Parameters
    ----------
    frame : frameobject
        Contains the info about the current local variables values.
    fnc : function
        The function to inspect
    output_strategy : string, optional
        Controls the behavior of the IOIterator for output paths.
    mix_names : bool, optional
        Whether or not to append a mix of input names at the beginning.

    Returns
    -------
        Properly instantiated IOIterator object.

    """

    # Create a new object that does not contain the ``self`` dict item
    def _selfless_dict(_values):
        return {key: val for key, val in _values.items() if key != "self"}

    args, _, _, values = inspect.getargvalues(frame)
    args.remove("self")
    # Create a new object that does not contain the ``self`` dict item from the
    # provided copy of the local symbol table returned by ``getargvalues``.
    # Avoids attempting to remove it from the object returned by
    # ``getargvalues``.
    values = _selfless_dict(values)

    spargs, defaults = get_args_default(fnc)

    len_args = len(spargs)
    len_defaults = len(defaults)
    split_at = len_args - len_defaults

    inputs = []
    outputs = []
    out_dir = ""

    # inputs
    for arv in args[:split_at]:
        inputs.append(values[arv])

    # defaults
    out_keys = []
    for arv in args[split_at:]:
        if arv == "out_dir":
            out_dir = values[arv]
        elif "out_" in arv:
            out_keys.append(arv)
            outputs.append(values[arv])

    return io_iterator(
        inputs,
        out_dir,
        outputs,
        output_strategy=output_strategy,
        mix_names=mix_names,
        out_keys=out_keys,
    )


class IOIterator:
    """Create output filenames that work nicely with multiple input files from
    multiple directories (processing multiple subjects with one command)

    Use information from input files, out_dir and out_fnames to generate
    correct outputs which can come from long lists of multiple or single
    inputs.
    """

    @warning_for_keywords()
    def __init__(self, *, output_strategy="absolute", mix_names=False):
        self.output_strategy = output_strategy
        self.mix_names = mix_names
        self.inputs = []
        self.out_keys = None

    def set_inputs(self, *args):
        self.file_existence_check(args)
        self.input_args = list(args)
        for inp in self.input_args:
            if isinstance(inp, (str, Path)):
                _inp = _resolve_path_or_pattern(inp)
                self.inputs.append(_inp)
            if isinstance(inp, list) and all(isinstance(s, (str, Path)) for s in inp):
                _nested = []
                for i in inp:
                    if not isinstance(i, (str, Path)):
                        continue
                    _nested.append(_resolve_path_or_pattern(i))
                self.inputs.append(list(itertools.chain.from_iterable(_nested)))

    def set_out_dir(self, out_dir):
        self.out_dir = out_dir

    def set_out_fnames(self, *args):
        self.out_fnames = list(args)

    def set_output_keys(self, *args):
        self.out_keys = list(args)

    def create_outputs(self):
        if len(self.inputs) >= 1:
            self.updated_inputs, self.outputs = connect_output_paths(
                self.inputs,
                self.out_dir,
                self.out_fnames,
                output_strategy=self.output_strategy,
                mix_names=self.mix_names,
            )

            self.create_directories()

        else:
            raise ImportError("No inputs")

    def create_directories(self):
        for outputs in self.outputs:
            for output in outputs:
                directory = Path(output).parent
                if not (directory == "" or directory.exists()):
                    os.makedirs(directory)

    def __iter__(self):
        ins = np.array(self.inputs).T
        out = np.array(self.outputs)
        IO = np.concatenate([ins, out], axis=1)
        for i_o in IO:
            if len(i_o) == 1:
                item = i_o[0]
                yield item if isinstance(item, Path) else str(item)
            else:
                yield i_o

    def file_existence_check(self, args):
        input_args = []
        for fname in args:
            if isinstance(fname, (str, Path)):
                input_args.append(fname)
            # unpack variable string
            if isinstance(fname, list) and all(
                isinstance(s, (str, Path)) for s in fname
            ):
                input_args += fname
        for path in input_args:
            paths = _resolve_path_or_pattern(path)
            if len(paths) == 0:
                raise OSError(f"File not found: {path}")
