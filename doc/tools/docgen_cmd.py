#!/usr/bin/env python3
"""
Script to generate documentation for command line utilities
"""
import importlib
import inspect
import os
from os.path import join as pjoin
from subprocess import PIPE, CalledProcessError, Popen
import sys

# version comparison
# from packaging.version import Version

# List of workflows to ignore
SKIP_WORKFLOWS_LIST = ("Workflow", "CombinedWorkflow")


def sh3(cmd):
    """
    Execute command in a subshell, return stdout, stderr
    If anything appears in stderr, print it out to sys.stderr

    https://github.com/scikit-image/scikit-image/blob/master/doc/gh-pages.py

    Copyright (C) 2011, the scikit-image team All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    Redistributions of source code must retain the above copyright notice,
    this list of conditions and the following disclaimer.
    Redistributions in binary form must reproduce the above copyright notice,
    this list of conditions and the following disclaimer in the documentation
    and/or other materials provided with the distribution.
    Neither the name of skimage nor the names of its contributors may be used
    to endorse or promote products derived from this software without specific
    prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
    IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
    OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
    IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
    INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
    BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
    USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
    THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
    THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
    """
    p = Popen(cmd, stdout=PIPE, stderr=PIPE, shell=True)
    out, err = p.communicate()
    retcode = p.returncode
    if retcode:
        raise CalledProcessError(retcode, cmd)
    else:
        return out.rstrip(), err.rstrip()


def abort(error):
    print(f"*WARNING* Command line API documentation not generated: {error}")
    exit()


def get_doc_parser(class_obj):
    # return inspect.getdoc(class_obj.run)
    try:
        ia_module = importlib.import_module("dipy.workflows.base")
        parser = ia_module.IntrospectiveArgumentParser()
        parser.add_workflow(class_obj())
    except Exception as e:
        abort(f"Error on {class_obj.__name__}: {e}")

    return parser


def format_title(text):
    text = text.title()
    line = "-" * len(text)
    return f"{text}\n{line}\n\n"


if __name__ == "__main__":
    # package name: Eg: dipy
    package = sys.argv[1]
    # directory in which the generated rst files will be saved
    outdir = sys.argv[2]

    try:
        __import__(package)
    except ImportError:
        abort(f"Can not import {package}")

    # NOTE: with the new versioning scheme, this check is not needed anymore
    # Also, this might be needed if we do not use spin to generate the docs
    # module = sys.modules[package]

    # Check that the source version is equal to the installed
    # version. If the versions mismatch the API documentation sources
    # are not (re)generated. This avoids automatic generation of documentation
    # for older or newer versions if such versions are installed on the system.

    # installed_version = Version(module.__version__)

    # info_file = pjoin('..', package, 'info.py')
    # info_lines = open(info_file).readlines()
    # source_version = '.'.join(
    #     [v.split('=')[1].strip(" '\n.")
    #      for v in info_lines
    #      if re.match('^_version_(major|minor|micro|extra)', v)]).strip('.')
    # source_version = Version(source_version)
    # print('***', source_version)

    # if source_version != installed_version:
    #     print('***', installed_version)
    #     abort("Installed version does not match source version")

    # generate docs
    command_list = []

    workflow_module = importlib.import_module("dipy.workflows.workflow")
    cli_module = importlib.import_module("dipy.workflows.cli")

    workflows_dict = getattr(cli_module, "cli_flows")

    workflow_desc = {}
    # We get all workflows class obj in a dictionary
    for path_file in os.listdir(pjoin("..", "dipy", "workflows")):
        module_name = inspect.getmodulename(path_file)
        if module_name is None:
            continue

        module = importlib.import_module(f"dipy.workflows.{module_name}")
        members = inspect.getmembers(module)
        d_wkflw = {name: {"module": obj, "parser": get_doc_parser(obj)}
                   for name, obj in members
                   if inspect.isclass(obj) and
                   issubclass(obj, workflow_module.Workflow) and
                   name not in SKIP_WORKFLOWS_LIST
                   }

        workflow_desc.update(d_wkflw)

    cmd_list = []
    for fname, wflw_value in workflows_dict.items():
        flow_module_name, flow_name = wflw_value

        print(f"Generating docs for: {fname} ({flow_name})")
        out_fname = fname + ".rst"
        with open(pjoin(outdir, out_fname), "w", encoding="utf-8") as fp:
            dashes = "=" * len(fname)
            fp.write(f".. {fname}:\n\n{dashes}\n{fname}\n{dashes}\n\n")
            parser = workflow_desc[flow_name]["parser"]
            if parser.description not in ["", "\n\n"]:
                fp.write(format_title("Synopsis"))
                fp.write(f"{parser.description}\n\n")
            fp.write(format_title("usage"))
            str_p_args = " ".join([p[0] for p in parser.positional_parameters]).lower()
            fp.write(".. code-block:: bash\n\n")
            fp.write(f"    {fname} [OPTIONS] {str_p_args}\n\n")
            fp.write(format_title("Input Parameters"))
            for p in parser.positional_parameters:
                fp.write(f"* ``{p[0]}``\n\n")
                comment = '\n  '.join([text.rstrip() for text in p[2]])
                fp.write(f"  {comment}\n\n")

            optional_params = [p for p in parser.optional_parameters
                               if not p[0].startswith("out_")]
            if optional_params:
                fp.write(format_title("General Options"))
                for p in optional_params:
                    fp.write(f"* ``--{p[0]}``\n\n")
                    comment = '\n  '.join([text.rstrip() for text in p[2]])
                    fp.write(f"  {comment}\n\n")

            if parser.output_parameters:
                fp.write(format_title("Output Options"))
                for p in parser.output_parameters:
                    fp.write(f"* ``--{p[0]}``\n\n")
                    comment = '\n  '.join([text.rstrip() for text in p[2]])
                    fp.write(f"  {comment}\n\n")

            if parser.epilog:
                fp.write(format_title("References"))
                fp.write(parser.epilog.replace("References: \n", ""))
        cmd_list.append(out_fname)
        print("Done")

    # generate index.rst
    print("Generating index.rst")
    with open(pjoin(outdir, "index.rst"), "w") as index:
        index.write(".. _workflows_reference:\n\n")
        index.write("Command Line Utilities Reference\n")
        index.write("================================\n\n")
        index.write(".. toctree::\n\n")
        for cmd in cmd_list:
            index.write(f"   {cmd}")
            index.write("\n")
    print("Done")
