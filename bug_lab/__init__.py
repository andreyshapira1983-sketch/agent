"""bug_lab — sandbox with a deliberate bug for self-repair test runs.

This module is NOT used in production. It exists so a live agent can
be asked: "run the tests under bug_lab/, find the failure, point at
the line that's wrong". The point is to exercise run_tests +
read_logs + file_read working together, not to ship a feature.
"""
