"""
This file is derived from ``tools.py`` of Lab (<https://lab.readthedocs.io>).
Functions and classes that are not needed for this project were removed.
"""
import errno
import logging
import os
import pickle
import pprint
import random
import re
import resource
import subprocess
import sys
import time


DEFAULT_ENCODING = "utf-8"


def get_string(s):
    if isinstance(s, bytes):
        return s.decode(DEFAULT_ENCODING)
    else:
        raise ValueError("tools.get_string() only accepts byte strings")


def get_script_path():
    """Get absolute path to main script."""
    return os.path.abspath(sys.argv[0])


def get_python_executable():
    return sys.executable or "python"


def configure_logging(level=logging.INFO):
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

# TODO: only used by a deprecated parser function. will be removed.
def make_list(value):
    if value is None:
        return []
    elif isinstance(value, list):
        return value[:]
    elif isinstance(value, (tuple, set)):
        return list(value)
    else:
        return [value]


def makedirs(path):
    """
    os.makedirs() variant that doesn't complain if the path already exists.
    """
    try:
        os.makedirs(path)
    except OSError:
        # Directory probably already exists.
        pass


def write_state(state, file_path):
    with open(file_path, "wb") as state_file:
        pickle.dump(state, state_file)


def read_state(file_path, wait_time, repetitions):
    for _ in range(repetitions):
        time.sleep(wait_time * random.random())
        if os.path.exists(file_path):
            with open(file_path, "rb") as state_file:
                return pickle.load(state_file)
    else:
        logging.critical(f"Could not find file '{file_path}' after {repetitions} attempts.")


class SubmissionError(Exception):
    def __init__(self, cpe):
        self.returncode = cpe.returncode
        self.cmd = cpe.cmd
        self.output = cpe.output
        self.stdout = cpe.stdout
        self.stderr = cpe.stderr

    def __str__(self):
        return f"""
                Error during job submission:
                Submission command: {self.cmd}
                Returncode: {self.returncode}
                Output: {self.output}
                Captured stdout: {self.stdout}
                Captured stderr: {self.stderr}"""

    def warn(self):
        logging.warning(f"The following batch submission failed but is "
                        f"ignored: {self}")

    def warn_abort(self):
        logging.error(
            f"Task order cannot be kept because the following batch "
            f"submission failed: {self} Aborting search.")


class TaskError(Exception):
    def __init__(self, critical_tasks):
        self.critical_tasks = critical_tasks
        self.indices_critical = [int(parts[1]) for parts in (
            task_id.split("_") for task_id in self.critical_tasks)]

    def __repr__(self):
        return pprint.pformat(self.critical_tasks)

    def remove_critical_tasks(self, job):
        """Remove tasks from job that entered a critical state."""
        job["tasks"] = [t for i, t in enumerate(
            job["tasks"]) if i not in self.indices_critical]
        logging.warning(
            f"Some tasks from job {job['id']} entered a critical "
            f"state but the search is continued.")

    def remove_tasks_after_first_critical(self, job):
        """
        Remove all tasks from job after the first one that entered a
        critical state.
        """
        first_failed = self.indices_critical[0]
        job["tasks"] = job["tasks"][:first_failed]
        if not job["tasks"]:
            logging.error("Since the first task failed, the order "
                          "cannot be kept. Aborting search.")
        else:
            logging.warning(
                f"At least one task from job {job['id']} entered a "
                f"critical state: {self} The tasks before the first "
                f"critical one are still considered.")


class PollingError(Exception):
    def __init__(self, job_id):
        self.job_id = job_id

    def warn_abort(self):
        logging.error(f"Polling job {self.job_id} caused an error. "
                      f"Aborting search.")

# This function is copied from lab.calls.call (<https://lab.readthedocs.org>).
def _set_limit(kind, soft_limit, hard_limit):
    try:
        resource.setrlimit(kind, (soft_limit, hard_limit))
    except (OSError, ValueError) as err:
        logging.critical(
            f"Resource limit for {kind} could not be set to "
            f"[{soft_limit}, {hard_limit}] ({err})"
        )


def parse(content, pattern, type=int):
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


class Run:
    """Define an executable command with time and memory limits.
    """

    def __init__(self, command, time_limit=1800, memory_limit=None, log_output=None):
        """*command* is a list of strings that starts your program with
        the desired parameters on a Linux machine.

        After *time_limit* seconds, the subprocess of *command*
        is killed.

        Above a memory usage of *memory_limit* MiB, the subprocess of
        *command* is killed.

        Use the *log_output* option ``"on_fail"`` if you want log files to be
        written when *command* terminates on a non-zero exit code or use the
        option ``"always"`` if you want them always to be written. These options
        only work in combination with the :func:`machetli.run.run_all` function.
        """
        self.command = command
        self.time_limit = time_limit
        self.memory_limit = memory_limit
        self.log_on_fail = log_output == "on_fail"
        self.log_always = log_output == "always"

    def __repr__(self):
        return f'Run(\"{" ".join([os.path.basename(part) for part in self.command])}\")'

    def start(self):
        """Format the command with the entries of *state* and execute it with
        `subprocess.Popen <https://docs.python.org/3/library/subprocess.html#subprocess.Popen>`_.
        Return the 3-tuple (stdout, stderr, returncode) with the values obtained
        from the executed command.
        """
        # These declarations are needed for the _prepare_call() function.
        time_limit = self.time_limit
        memory_limit = self.memory_limit

        def _prepare_call():
            # When the soft time limit is reached, SIGXCPU is emitted. Once we
            # reach the higher hard time limit, SIGKILL is sent. Having some
            # padding between the two limits allows programs to handle SIGXCPU.
            if time_limit is not None:
                _set_limit(resource.RLIMIT_CPU, time_limit, time_limit + 5)
            if memory_limit is not None:
                _, hard_mem_limit = resource.getrlimit(resource.RLIMIT_AS)
                # Convert memory from MiB to Bytes.
                _set_limit(resource.RLIMIT_AS, memory_limit *
                           1024 * 1024, hard_mem_limit)
            _set_limit(resource.RLIMIT_CORE, 0, 0)

        logging.debug(f"Command:\n{self.command}")

        try:
            process = subprocess.Popen(self.command,
                                       preexec_fn=_prepare_call,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       text=True)
        except OSError as err:
            if err.errno == errno.ENOENT:
                cmd = " ".join(self.command)
                logging.critical(f"Error: Call '{cmd}' failed. "
                                 "One of the files was not found.")
            else:
                raise

        out_str, err_str = process.communicate()

        return (out_str, err_str, process.returncode)


class RunWithInputFile(Run):
    """Extension of the :class:`Run <machetli.run.Run>` class adding
    the option of sending the content of a file to stdin.
    """
    # e.g., in a command like ``path/to/./my_executable < my_input_file``.

    def __init__(self, command, input_file, **kwargs):
        """*input_file* is the path to the file whose content should be sent to
        the stdin of the executed *command*.
        """
        super().__init__(command, **kwargs)
        self.input_file = input_file

    def start(self):
        """Same as the :meth:`base method <machetli.run.Run.start>`, with
        the addition of the content from *input_file* being passed to the
        stdin of the executed *command*.
        """
        # These declarations are needed for the _prepare_call() function.
        time_limit = self.time_limit
        memory_limit = self.memory_limit

        def _prepare_call():
            # When the soft time limit is reached, SIGXCPU is emitted. Once we
            # reach the higher hard time limit, SIGKILL is sent. Having some
            # padding between the two limits allows programs to handle SIGXCPU.
            if time_limit is not None:
                _set_limit(resource.RLIMIT_CPU, time_limit, time_limit + 5)
            if memory_limit is not None:
                _, hard_mem_limit = resource.getrlimit(resource.RLIMIT_AS)
                # Convert memory from MiB to Bytes.
                _set_limit(resource.RLIMIT_AS, memory_limit *
                           1024 * 1024, hard_mem_limit)
            _set_limit(resource.RLIMIT_CORE, 0, 0)

        try:
            process = subprocess.Popen(self.command,
                                       preexec_fn=_prepare_call,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       stdin=subprocess.PIPE,
                                       text=True)
        except OSError as err:
            if err.errno == errno.ENOENT:
                cmd = " ".join(self.command)
                logging.critical(f"Error: Call '{cmd}' failed. "
                                 "One of the files was not found.")
            else:
                raise

        with open(self.input_file, "r") as file:
            input_text = file.read()

        out_str, err_str = process.communicate(input=input_text)

        return (out_str, err_str, process.returncode)


def run_all(state):
    """Start all runs in *state["runs"]* and return a *results* dictionary
    where run outputs of run *run_name* can be accessed via:

    - *results[run_name]["stdout"]*,
    - *results[run_name]["stderr"]* and
    - *results[run_name]["returncode"]*.
    """
    assert "runs" in state, "Could not find entry \"runs\" in state."
    results = {}
    for name, run in state["runs"].items():
        stdout, stderr, returncode = run.start(state)
        if run.log_always or run.log_on_fail and returncode != 0:
            cwd = state["cwd"] if "cwd" in state else os.path.dirname(
                get_script_path())
            if stdout:
                with open(os.path.join(cwd, f"{name}.log"), "w") as logfile:
                    logfile.write(stdout)
            if stderr:
                with open(os.path.join(cwd, f"{name}.err"), "w") as errfile:
                    errfile.write(stderr)
        results.update(
            {name: {"stdout": stdout, "stderr": stderr, "returncode": returncode}}
        )
    return results


def run_and_parse_all(state, parsers):
    """Execute :func:`machetli.run.run_all` and apply all *parsers* to the
    generated stdout and stderr outputs. Return an updated version of the
    *results* dictionary containing the parsing results in place of the actual
    stdout and stderr outputs. *parsers* can be a list of :class:`machetli.parser.Parser`
    objects or a single one.
    """
    results = run_all(state)
    parsed_results = {}
    parsers = [parsers] if not isinstance(parsers, list) else parsers
    for name, result in results.items():
        parsed_results.update(
            {name: {"stdout": {}, "stderr": {},
                    "returncode": result["returncode"]}}
        )
        for parser in parsers:
            parsed_results[name]["stdout"].update(
                parser.parse(name, result["stdout"]))
            parsed_results[name]["stderr"].update(
                parser.parse(name, result["stderr"]))
    parsed_results["raw_results"] = results
    return parsed_results
