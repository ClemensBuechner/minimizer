"""
This module is derived from ``tools.py`` of Lab (<https://lab.readthedocs.io>).
Functions and classes that are not needed for this project were removed.
"""
import itertools
import logging
import os
from pathlib import Path
import pickle
import re
import resource
import shutil
import subprocess
import sys


DEFAULT_ENCODING = "utf-8"


# From https://docs.python.org/3/library/itertools.html#itertools-recipes
def batched(iterable, n):
    """Batch data into tuples of length n. The last batch may be shorter.

    :Example:

    .. code-block:: python

        batched('ABCDEFG', 3) # --> ABC DEF G


    """
    if n < 1:
        raise ValueError(f'batch size was {n=} but must be at least one')
    it = iter(iterable)
    while batch := tuple(itertools.islice(it, n)):
        yield batch


def get_script_path():
    """
    Get absolute path to main script, or the current working directory, if the
    Python session is interactive.
    """
    return Path(sys.argv[0]).absolute()


def get_python_executable():
    """
    Get path to the main Python executable.
    """
    return sys.executable or shutil.which("python")


def configure_logging(level=logging.INFO):
    """
    Set up internal loggers to only print messages at least as important as the
    given log level.Warnings and error messages will be printed on
    stderr, and critical messages will terminate the program.
    All messages will be prefixed with the current time.
    """
    # Python adds a default handler if some log is written before this
    # function is called. We therefore remove all handlers that have
    # been added automatically.
    root_logger = logging.getLogger("")
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)

    class ErrorAbortHandler(logging.StreamHandler):
        """
        Logging handler that exits when a critical error is encountered.
        """

        def emit(self, record):
            logging.StreamHandler.emit(self, record)
            if record.levelno >= logging.CRITICAL:
                sys.exit("aborting")

    class StdoutFilter(logging.Filter):
        def filter(self, record):
            return record.levelno <= logging.WARNING

    class StderrFilter(logging.Filter):
        def filter(self, record):
            return record.levelno > logging.WARNING

    formatter = logging.Formatter("%(asctime)-s %(levelname)-8s %(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(StdoutFilter())

    stderr_handler = ErrorAbortHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(StderrFilter())

    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(stderr_handler)
    root_logger.setLevel(level)


def write_state(state, file_path):
    """
    Use pickle to write a given state to disk.
    """
    with open(file_path, "wb") as state_file:
        pickle.dump(state, state_file)


def read_state(file_path):
    """
    Use pickle to read a state from disk.
    """
    with open(file_path, "rb") as state_file:
        return pickle.load(state_file)


def parse(content, pattern, type=int):
    r"""
    Look for matches of *pattern* in *content*. If any matches are found, the
    first group present in the regular expression is cast as *type* and
    returned.

    :Example:

    .. code-block:: python

        content = '''
        Runtime: 23.5s
        Heuristic value: 42
        Search successful
        '''
        t = parse(content, r"Runtime: (\.+)s", float)
        h = parse(content, r"Heuristic value: (\d+)", int)


    """
    if type == bool:
        logging.warning(
            "Casting any non-empty string to boolean will always "
            "evaluate to true. Are you sure you want to use type=bool?"
        )

    regex = re.compile(pattern)
    match = regex.search(content)
    if match:
        try:
            value = match.group(1)
        except IndexError:
            logging.critical(f"Regular expression '{regex}' has no groups.")
        else:
            return type(value)
    else:
        logging.debug(f"Failed to find pattern '{regex}'.")


# TODO: Properly provide interface in style of subprocess.run.
def run_with_limits(command, time_limit=1800, memory_limit=None,
                    log_output=None, input_file=None, stdout=None, stderr=None):
    """
    Run an executable command with time and memory limits.

    :param command: is a list of strings defining the command to execute. For
        details, see the Python module
        `subprocess <https://docs.python.org/3/library/subprocess.html>`_.

    :param time_limit: time in seconds after which the command is terminated.
        Because states are evaluated in sequence in Machetli, it is important
        to use resource limits to make sure a command eventually terminates.

    :param memory_limit: memory limit in MiB to use for executing the command.

    :param log_output:
        # TODO: there is no longer a method *start*, so this documentation needs
        #  to be updated.
        the method :meth:`start` will return whatever the command writes to
        stdout and stderr as strings. However, this log output will not be
        written to the main log or to disk, unless you specify it otherwise in
        this option. Use the *log_output* option ``"on_fail"`` if you want log
        files to be written when *command* terminates on a non-zero exit code or
        use the option ``"always"`` if you want them always to be written.

        .. note:: This option currently does not work and is ignored.

    :param input_file:
        in case the process takes input on stdin, you can pass a path to a file
        here that will be piped to stdin of the process. With the default value
        of `None`, nothing is passed to stdin.

    """
    log_on_fail = log_output == "on_fail"
    log_always = log_output == "always"

    # This function is copied from lab.calls.call
    # (<https://github.com/aibasel/lab>).
    def _set_limit(kind, soft_limit, hard_limit):
        try:
            resource.setrlimit(kind, (soft_limit, hard_limit))
        except (OSError, ValueError) as err:
            logging.critical(
                f"Resource limit for {kind} could not be set to "
                f"[{soft_limit}, {hard_limit}] ({err})"
            )

    def _prepare_call():
        # When the soft time limit is reached, SIGXCPU is emitted. Once we
        # reach the higher hard time limit, SIGILL is sent. Having some
        # padding between the two limits allows programs to handle SIGXCPU.
        if time_limit is not None:
            _set_limit(resource.RLIMIT_CPU, time_limit, time_limit + 5)
        if memory_limit is not None:
            _, hard_mem_limit = resource.getrlimit(resource.RLIMIT_AS)
            # Convert memory from MiB to Bytes.
            _set_limit(resource.RLIMIT_AS, memory_limit *
                       1024 * 1024, hard_mem_limit)
        _set_limit(resource.RLIMIT_CORE, 0, 0)

    logging.debug(f"Command:\n{command}")

    stdin = subprocess.PIPE if input_file else None
    process = subprocess.Popen(command,
                               preexec_fn=_prepare_call,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               stdin=stdin,
                               text=True)
    input_text = None
    if input_file:
        with open(input_file, "r") as file:
            input_text = file.read()

    out_str, err_str = process.communicate(input=input_text)

    # TODO: The following block stems from *run_all* and we might want to
    #  reuse some of its logic.
    # if log_always or log_on_fail and returncode != 0:
    #     cwd = state["cwd"] if "cwd" in state else os.path.dirname(
    #         get_script_path())
    #     if stdout:
    #         with open(os.path.join(cwd, f"{name}.log"), "w") as logfile:
    #             logfile.write(stdout)
    #     if stderr:
    #         with open(os.path.join(cwd, f"{name}.err"), "w") as errfile:
    #             errfile.write(stderr)

    return out_str, err_str, process.returncode
