.. _commit-codes:

:octicon:`git-commit;1em;sd-text-info` Commit message codes
-----------------------------------------------------------

Please prefix all commit summaries with one (or more) of the following labels.
This should help others to easily classify the commits into meaningful
categories:

* *BF* : bug fix
* *RF* : refactoring
* *NF* : new feature
* *BW* : addresses backward-compatibility
* *OPT* : optimization
* *CI* : continuous integration
* *MNT* : maintenance tasks, such as release preparation
* *DOC*: for all kinds of documentation related commits
* *TEST* : for adding or changing tests
* *STYLE* : PEP8 conformance, whitespace changes etc that do not affect
  function.

So your commit message might look something like this::

    TEST: Relax test threshold slightly

    Attempted fix for failure on windows test run when arrays are in fact
    very close (within 6 dp).

Keeping up a habit of doing this is useful because it makes it much easier to
see at a glance which changes are likely to be important when you are looking
for sources of bugs, fixes, large refactorings or new features.
