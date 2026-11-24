param(
    [int]$ThroughId = 2351,
    [int]$BatchSize = 10,
    [double]$AnnIntervalSeconds = 2.0,
    [int]$RelationshipPageLimit = 100
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CheckpointPath = Join-Path $ProjectRoot 'data\plans\ann_full_sweep_checkpoint.txt'

if (-not (Test-Path -LiteralPath $CheckpointPath)) {
    [System.IO.File]::WriteAllText($CheckpointPath, '1')
}
$LastCompletedId = 0
$CheckpointText = (Get-Content -LiteralPath $CheckpointPath -Raw).Trim()
if ($CheckpointText -match '^\d+$') {
    $LastCompletedId = [int]$CheckpointText
}

for ($StartId = $LastCompletedId + 1; $StartId -le $ThroughId; $StartId += $BatchSize) {
    $EndId = [Math]::Min($ThroughId, $StartId + $BatchSize - 1)
    Write-Output "START work IDs $StartId-$EndId"

    & python (Join-Path $PSScriptRoot 'enrich_ann_rag_direct.py') `
        --start-id $StartId --end-id $EndId --limit $BatchSize --interval $AnnIntervalSeconds
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Direct ANN enrichment failed for work IDs $StartId-$EndId (exit $LASTEXITCODE). Checkpoint remains $LastCompletedId."
        exit $LASTEXITCODE
    }

    & python (Join-Path $PSScriptRoot 'enrich_ann_relationship_graph.py') `
        --start-id $StartId --end-id $EndId --interval $AnnIntervalSeconds `
        --max-depth 2 --max-pages-per-work $RelationshipPageLimit --root-limit 1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ANN relationship expansion failed for work IDs $StartId-$EndId (exit $LASTEXITCODE). Checkpoint remains $LastCompletedId."
        exit $LASTEXITCODE
    }

    [System.IO.File]::WriteAllText($CheckpointPath, [string]$EndId)
    $LastCompletedId = $EndId
    Write-Output "CHECKPOINT $LastCompletedId/$ThroughId"
}

Write-Output "SWEEP COMPLETE through work ID $LastCompletedId"
