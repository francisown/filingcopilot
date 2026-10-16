# GitHub publishing notes

These are preparation instructions for `francisown`; they do not create or publish a
repository. The suggested repository name is **filingcopilot**. If you choose another
name, update the example remote URL before using it.

## Repository presentation

Choose **Public** so readers can view the code and documentation without an invitation.
Use this English description in the repository's About section:

> Research stocks with financial data, price charts, and AI answers linked to original filings.

Suggested topics:

`financial-analysis`, `stock-research`, `sec-edgar`, `financial-statements`,
`equity-research`, `rag`, `ai-agents`, `mcp`, `python`, `fastapi`

Topics describe the project and help readers browse related repositories; they do not
guarantee traffic. See [GitHub's topic documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics).

The main `README.md` is English, with a Chinese link and a labelled interface example.
No CI badge is included: confirm the actual Actions runs after uploading before claiming
that the hosted checks pass. A running public demo has not been deployed.

## Publish the selected history

Use the prepared **clean source checkout**, not the original development checkout. The
clean checkout contains the selected source and a single root commit, with no old branches
or remote. The original checkout retains private caches and older development history.

Create an empty `francisown/filingcopilot` repository on GitHub. If you plan to push the
prepared history, do not initialize the remote with another README or license.
From the clean source checkout, inspect it before connecting the remote:

```bash
git status --short
git branch --all
git rev-list --count --all
```

The worktree should be clean, and the prepared history should contain one commit.
Then publish that history alone:

```bash
git remote add origin https://github.com/francisown/filingcopilot.git
git push -u origin HEAD:main
```

These commands are for you to run after creating the repository. Do not push all branches
or mirror the original development repository. If using GitHub Desktop, add the clean
checkout as the local repository before choosing Publish repository.

A source ZIP is another distribution format. Extract it to inspect its contents; uploading
the ZIP alone does not give visitors a browsable source tree or rendered project README.
Source and package publication do not require deploying the web app or uploading to PyPI.

## After uploading

- Confirm the default branch renders the English README and its screenshot.
- Set the About description and topics, then check the Chinese guide link.
- Confirm `.env`, private configuration, model caches, and old research outputs are absent.
- Check the Actions runs on GitHub; local results are not hosted CI results.
- Use the English issue templates to collect reproducible feedback.

GitHub recognises a root README and supports repository-relative image links.
See [the README documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
and [issue-template documentation](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates).
