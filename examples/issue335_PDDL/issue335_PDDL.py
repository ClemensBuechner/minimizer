#!/usr/bin/env python3

import os
import platform
import sys

from minimizer import tools
from minimizer.grid import environments
from minimizer.planning import auxiliary
from minimizer.planning.generators import RemoveObjects, ReplaceLiteralsWithTruth
from minimizer.planning.pddl_writer import write_PDDL
from minimizer.search import search
from minimizer.tools import get_script_path

# The Fast Downward issue we use for this example is from 2014. The code of the
# planner from that time is only compatible with Python versions < 3.8.
if "PYTHON_3_7" not in os.environ:
    msg = """
Make sure to set the environment variable PYTHON_3_7 to the path to a
Python 3.7 executable (we need this due to an older Fast Downward version).
    """
    sys.exit(msg)
elif "DOWNWARD_REPO" not in os.environ:
    msg = """
Make sure to set the environment variable DOWNWARD_REPO to the path to a Fast
Downward repository (https://github.com/aibasel/downward) at commit 09ccef5fd.
    """
    sys.exit(msg)

script_path = tools.get_script_path()
script_dir = os.path.dirname(script_path)

domain_filename = os.path.join(script_dir, "cntr-domain.pddl")
problem_filename = os.path.join(script_dir, "cntr-problem.pddl")

# Here, we define the initial state the search should be started from. Generally, you can
# store anything in this dictionary that could be useful for the minimization task.
initial_state = {
    # We are  creating the entry "pddl_task" because our successor generators
    # and our evaluator expect this key.
    "pddl_task": auxiliary.parse_pddl_task(domain_filename, problem_filename),
}
successor_generators = [RemoveObjects(), ReplaceLiteralsWithTruth()]
evaluator_filename = os.path.join(script_dir, "evaluator.py")

# The defined environment depends on where you want to execute the search:
#   - on you local machine
#   - on a Slurm computing grid
environment = environments.LocalEnvironment()
if platform.node().endswith((".scicore.unibas.ch", ".cluster.bc2.ch")):
    environment = environments.BaselSlurmEnvironment(
        export=["PYTHON_3_7", "DOWNWARD_REPO"])

# To start the search, we need to pass the initial state, the successor
# generator(s), the evaluator class and the environment to be used to the
# main function, which will return the resulting state, once the search
# is finished.
result = search(initial_state, successor_generators, evaluator_filename, environment)

# If you want the modified PDDL task to be dumped to files (which you
# probably do!), you need to explicitly do this here. Otherwise, it
# will fall prey to the garbage collector when this script ends!
write_PDDL(result["pddl_task"], "result-domain.pddl", "result-problem.pddl")

# A note on successor generators:
# For this example, we chose to use two successor generators (RemoveObjects
# and ReplaceLiteralsWithTruth) that are already implemented and can be used
# to transform PDDL tasks. Implementations of the SuccesssorGenerator class
# are required to have a *get_successors(state)* function that returns a generator
# of states which are considered the successors. The way a successor generator
# is implemented determines how the state and your run(s) look(s) after the search
# is completed. Like the evaluator, successor generators may have to be
# tailored exactly to your use case.
