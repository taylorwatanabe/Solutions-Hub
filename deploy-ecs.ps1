# Solutions Hub: build -> ECR push -> ECS rolling restart.
# Run from repo root:  .\deploy-ecs.cmd
# Or:  powershell -ExecutionPolicy Bypass -File .\deploy-ecs.ps1

param(
    [switch]$NoCache,
    [switch]$SkipEcs,
    [switch]$EcsOnly,
    [string]$Region = "us-east-2",
    [string]$Account = "422799216424",
    [string]$Repo = "solution-hub",
    [string]$Cluster = "Solution-Hub",
    [string]$Service = "Solution-Hub",
    [int]$PushMaxAttempts = 4,
    [int]$PushRetryDelaySec = 15,
    [int]$EcsMaxAttempts = 4,
    [int]$EcsRetryDelaySec = 15
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
        [string[]]$Command
    )

    & $Command[0] @($Command[1..($Command.Length - 1)])
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

$ECR = "${Account}.dkr.ecr.${Region}.amazonaws.com"
$expectedImage = "${ECR}/${Repo}:latest"

$buildId = ""
if (-not $EcsOnly) {
    Write-Host "Building in: $Root"
    try {
        $buildId = (git -C $Root rev-parse --short HEAD 2>$null | Out-String).Trim()
    }
    catch {
        $buildId = ""
    }
    if (-not $buildId) {
        $buildId = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    }
    Write-Host "Build id: $buildId"

    $imageUri = "${Repo}:latest"
    if ($NoCache) {
        Invoke-Checked "Docker build" docker build --no-cache --build-arg "BUILD_ID=$buildId" -t $imageUri .
    }
    else {
        Invoke-Checked "Docker build" docker build --build-arg "BUILD_ID=$buildId" -t $imageUri .
    }

    Write-Host "ECR login $ECR"
    aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR
    if ($LASTEXITCODE -ne 0) {
        throw "ECR login failed with exit code $LASTEXITCODE"
    }

    Invoke-Checked "Docker tag" docker tag "${Repo}:latest" $expectedImage
    Write-Host "Pushing $expectedImage (up to $PushMaxAttempts attempts)"
    $pushAttempt = 0
    while ($true) {
        $pushAttempt++
        docker push $expectedImage
        if ($LASTEXITCODE -eq 0) {
            break
        }
        if ($pushAttempt -ge $PushMaxAttempts) {
            throw "Docker push failed after $PushMaxAttempts attempts (last exit code $LASTEXITCODE)."
        }
        Write-Host "Docker push attempt $pushAttempt failed. Waiting ${PushRetryDelaySec}s..."
        Start-Sleep -Seconds $PushRetryDelaySec
    }
    Write-Host "Image pushed: $expectedImage"
}
else {
    Write-Host "EcsOnly: skipping build and ECR push; using $expectedImage"
}

if (-not $SkipEcs) {
    Write-Host "Forcing ECS deployment $Cluster / $Service"
    $ecsAttempt = 0
    while ($true) {
        $ecsAttempt++
        aws ecs update-service --cluster $Cluster --service $Service --force-new-deployment --region $Region --no-cli-pager
        if ($LASTEXITCODE -eq 0) {
            break
        }
        if ($ecsAttempt -ge $EcsMaxAttempts) {
            throw "ECS update-service failed after $EcsMaxAttempts attempts. Try: .\deploy-ecs.ps1 -EcsOnly"
        }
        Write-Host "ECS update attempt $ecsAttempt failed. Waiting ${EcsRetryDelaySec}s..."
        Start-Sleep -Seconds $EcsRetryDelaySec
    }
}
else {
    Write-Host "SkipEcs: image pushed; run .\deploy-ecs.ps1 -EcsOnly when AWS API is reachable."
}

Write-Host ""
Write-Host "Image: $expectedImage"
Write-Host "After deploy: open https://<host>/api/build-info and confirm build_id=$buildId"
Write-Host "Health check path: GET /healthz (or /health, /ping, /api/health) on port 8080"
