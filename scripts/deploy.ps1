<#
.SYNOPSIS
    Automated deployment script for KoreanTeacher_LINE on Google Cloud Run.

.DESCRIPTION
    Builds and deploys the containerized FastAPI Korean teacher service to Google Cloud Run.
    Ensures that the required Google Cloud Storage persistence bucket exists before triggering
    the Cloud Run deployment. The service authenticates to Google Cloud Secret Manager and
    Cloud Storage automatically using Application Default Credentials (ADC) associated with the
    Cloud Run service identity.

.PARAMETER ProjectId
    The Google Cloud Project ID. If omitted, defaults to the active project in gcloud config.

.PARAMETER AppName
    The target Cloud Run service name. Default: "korean-teacher-bot".

.PARAMETER Region
    The Google Cloud region for deployment. Default: "asia-northeast1" (Tokyo).

.PARAMETER BucketName
    The Google Cloud Storage bucket name for state and audit persistence.
    Defaults to "<project-id>-korean-teacher-data".

.EXAMPLE
    .\scripts\deploy.ps1

.EXAMPLE
    .\scripts\deploy.ps1 -ProjectId "my-gcp-project" -Region "asia-northeast1"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false, Position = 0, HelpMessage = "Google Cloud Project ID")]
    [string]$ProjectId,

    [Parameter(Mandatory = $false, HelpMessage = "Cloud Run service name")]
    [string]$AppName = "korean-teacher-bot",

    [Parameter(Mandatory = $false, HelpMessage = "Target deployment region")]
    [string]$Region = "asia-northeast1",

    [Parameter(Mandatory = $false, HelpMessage = "Cloud Storage bucket name for state persistence")]
    [string]$BucketName
)

$ErrorActionPreference = "Stop"

# ==============================================================================
# Step 1: Environment & Working Directory Resolution
# ==============================================================================
# Resolve repository root so this script executes predictably from any directory
$RepoRoot = Split-Path -Parent $PSScriptRoot
Write-Verbose "Repository root directory: $RepoRoot"

# Detect Google Cloud project if not explicitly supplied
if (-not $ProjectId) {
    Write-Host "Detecting active gcloud project..." -ForegroundColor Cyan
    $ProjectId = gcloud config get-value project 2>$null
    if (-not $ProjectId -or $ProjectId -eq "(unset)") {
        Write-Error "No Google Cloud project configured. Run 'gcloud config set project <id>' or pass -ProjectId."
        exit 1
    }
}
Write-Host "Target Google Cloud Project: $ProjectId" -ForegroundColor Green

# Set default bucket name based on project ID if not provided
if (-not $BucketName) {
    $BucketName = "$ProjectId-korean-teacher-data"
}

# ==============================================================================
# Step 2: Storage Bucket Initialization
# ==============================================================================
Write-Host "Verifying Google Cloud Storage bucket gs://$BucketName..." -ForegroundColor Cyan
$SyncScript = Join-Path $PSScriptRoot "sync_secrets.py"
$PythonCmd = "python"
if (Get-Command "py" -ErrorAction SilentlyContinue) {
    $PythonCmd = "py"
}
& $PythonCmd $SyncScript --init-bucket --project $ProjectId
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to initialize Cloud Storage bucket. Please check gcloud permissions."
    exit $LASTEXITCODE
}

# ==============================================================================
# Step 3: Google Cloud Run Deployment
# ==============================================================================
Write-Host "Deploying service '$AppName' to $Region (Cloud Run)..." -ForegroundColor Cyan
Write-Host "Source directory: $RepoRoot" -ForegroundColor Gray

# Deploy container image directly from source repository root
# - allow-unauthenticated: Enables public LINE webhook delivery over HTTPS
# - no-cpu-throttling: Ensures background tasks (TTS / audio generation) run smoothly
# - set-env-vars: Passes project ID and GCS bucket name to container runtime
gcloud run deploy $AppName `
    --source $RepoRoot `
    --region $Region `
    --allow-unauthenticated `
    --no-cpu-throttling `
    --set-env-vars "GOOGLE_CLOUD_PROJECT=$ProjectId,GCS_BUCKET_NAME=$BucketName" `
    --quiet

if ($LASTEXITCODE -eq 0) {
    Write-Host "Deployment completed successfully!" -ForegroundColor Green
    $ServiceUrl = gcloud run services describe $AppName --region $Region --format "value(status.url)" 2>$null
    if ($ServiceUrl) {
        Write-Host "Service URL: $ServiceUrl" -ForegroundColor Green
        Write-Host "Webhook Endpoint: $ServiceUrl/callback" -ForegroundColor Yellow
        Write-Host "Health Check: $ServiceUrl/health" -ForegroundColor Yellow
    }
} else {
    Write-Error "Cloud Run deployment failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}
