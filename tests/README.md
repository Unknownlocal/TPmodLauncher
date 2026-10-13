# Testing policy

Write tests that check meaningful behaviour, edge cases and failure handling.
Each test should catch a failure that matters to the launcher or hook manager.

- Preserve useful checks of behaviour that remains supported.
- Prefer real classes and objects. Mock only dependencies or conditions necessary
  to make a test practical and reliable, and keep each mock as small as possible.
- Use coverage reports to find gaps worth investigating. Add a test when the
  missing behaviour deserves a useful check, rather than to raise a percentage.
- Treat lost coverage as a reason for review, not an automatic failure. Removing
  obsolete code or replacing weak tests can legitimately change coverage. Check
  whether the change loses a useful test of supported behaviour.

Coverage measures which code ran; it does not prove that a test checks the right
result. There is no fixed minimum coverage percentage.
