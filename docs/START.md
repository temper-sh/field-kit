# Run a study

Clone `https://github.com/temper-sh/field-kit.git`, then start with
[the agent guide](ASSISTANT.md). Use installed Temper; the first command asks
it to install Field Kit's own Python. ./field-kit doctor then
performs read-only discovery.

The agent prepares a brief, exact candidates and a resource budget. After
approval it uses [the tools](TOOLS.md) to smoke, tune and investigate the
machine's useful limits. It follows [the research method](METHOD.md), reviews
completed work where needed, and [recovers and reports defects](REPAIR.md).

The returned archive includes evidence, conclusions and local repairs. Nothing
is shared automatically.
