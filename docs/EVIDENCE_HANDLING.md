# Public source and project evidence

The public distribution contains SDK implementation, general documentation and
explicitly authored synthetic fixtures. Keep project-specific source patches,
test logs, traces, conversations, host receipts, checkpoints, artifact hashes and
evaluation reports outside this repository. Replacing a project name alone does
not make its implementation details or captured dialogue suitable for publishing.

Public examples must label simulated authorization, checks and observations.
Use generic task/artifact identifiers and fixture revisions; do not relabel a
captured result as synthetic. Public contract checks establish implementation
behavior only, not measured live accuracy, task success or cost savings.

Before sharing a change:

1. Review the exact files and new commit ancestry. Branches containing private
   capture commits must not be pushed, even if a later commit deletes the files.
   Start from an already public base and copy only reviewed source changes.
2. Keep real project evidence outside the checkout. Ignore rules help prevent
   accidental staging; they cannot remove files that are already tracked.
3. Run `python scripts/check_public_distribution.py`. CI rejects tracked capture
   directories, result dumps, raw patches/logs and database files. This is a
   filename guard, not a guarantee that every text fixture is free of identifiers.
4. Review new text and generated package/archive contents before publishing.
   Package manifests separately exclude common raw evidence artifacts.

Deleting tracked evidence in a new commit removes it from the new source tree,
not from older commits, pull-request diffs, forks, cached copies or downloaded
archives. Historical cleanup or a repository visibility change needs a separate
repository-level decision; neither can recall copies already obtained by others.

公开版本只包含通用代码、文档和明确标注的合成数据。真实项目试验资料在仓库外
保管；新提交不引用私有试验报告或携带其历史提交。忽略规则、CI 文件检查和
打包排除共同防止误带入，但已公开的历史需要另行处理。
