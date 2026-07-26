# ruff: noqa: F821
base_cv = 0.01
head_cv = 0.01
max_attempts = 3
should_retry = not gate_result and attempt < max_attempts
