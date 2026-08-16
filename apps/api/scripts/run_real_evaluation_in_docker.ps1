[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Output,
    [string]$Dataset = "evals/datasets/agentic-rag-foundations-v1.json",
    [string]$CodeCommit,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$outputRoot = Join-Path $repositoryRoot "output"
$evaluatedPaths = @("apps/api", "evals/datasets")
$isRelativeOutput = -not [IO.Path]::IsPathRooted($Output)
if (-not $isRelativeOutput) {
    throw "Output must be a repository-relative path under output/."
}
$resolvedOutput = [IO.Path]::GetFullPath((Join-Path $repositoryRoot $Output))
$resolvedOutputRoot = [IO.Path]::GetFullPath($outputRoot)

if (-not $resolvedOutput.StartsWith($resolvedOutputRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Output must be located under the repository output directory."
}

$failureOutput = [IO.Path]::ChangeExtension($resolvedOutput, $null) + "-failure.json"
if ((Test-Path -LiteralPath $resolvedOutput) -or (Test-Path -LiteralPath $failureOutput)) {
    throw "The normal output path and its paired failure artifact path must both be unused."
}

Push-Location $repositoryRoot
try {
    $headCommit = (& git rev-parse HEAD).Trim()
    $changedEvaluatedFiles = & git status --porcelain -- $evaluatedPaths
}
finally {
    Pop-Location
}

if ($changedEvaluatedFiles) {
    throw "apps/api and evals/datasets must be clean before a provenance-bound real evaluation."
}
if ([string]::IsNullOrWhiteSpace($CodeCommit)) {
    $CodeCommit = $headCommit
}
if ($CodeCommit -ne $headCommit) {
    throw "CodeCommit must match the current HEAD used to build the evaluation image."
}

if (-not $SkipBuild) {
    & docker compose --project-directory $repositoryRoot build api
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$relativeOutput = $resolvedOutput.Substring($repositoryRoot.Length).TrimStart([char[]]@("\", "/")).Replace("\", "/")
$containerOutputVolume = "{0}:/app/output" -f $resolvedOutputRoot
$containerEvalsVolume = "{0}:/app/evals:ro" -f (Join-Path $repositoryRoot "evals")
& docker compose --project-directory $repositoryRoot run --rm --no-deps `
    --volume $containerOutputVolume `
    --volume $containerEvalsVolume `
    api python -m sourcetrace.evaluation.cli real `
    --dataset $Dataset `
    --code-commit $CodeCommit `
    --output "/app/$relativeOutput" `
    --confirm-real-provider
exit $LASTEXITCODE
