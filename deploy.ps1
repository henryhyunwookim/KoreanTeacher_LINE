# deploy.ps1
# This script deploys the application to Google Cloud Run

$ErrorActionPreference = "Stop"

Write-Host "Getting current gcloud project..."
$ProjectId = gcloud config get-value project 2>$null
if (-not $ProjectId) {
    Write-Error "No Google Cloud project configured. Please run 'gcloud init' first."
    exit 1
}
Write-Host "Deploying to project: $ProjectId"

Write-Host "Parsing .env file for secrets..."
$envVars = @()
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        $line = $_.Trim()
        # Ignore comments and empty lines
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line -split '=', 2
            $key = $parts[0].Trim()
            $value = $parts[1].Trim()
            # We don't quote here because gcloud expects KEY=VALUE
            $envVars += "$key=$value"
        }
    }
} else {
    Write-Warning ".env file not found. Deploying without environment variables."
}

$envString = $envVars -join ","

Write-Host "Starting gcloud run deploy..."
# Using --quiet to prevent interactive prompts if APIs need to be enabled
# but for the first time it might be better to let it log to shell so the user sees it.
# We will use variables for region and app name
$AppName = "korean-teacher-bot"
$Region = "asia-northeast1"

if ($envString) {
    gcloud run deploy $AppName `
        --source . `
        --region $Region `
        --allow-unauthenticated `
        --no-cpu-throttling `
        --set-env-vars $envString `
        --quiet
} else {
    gcloud run deploy $AppName `
        --source . `
        --region $Region `
        --allow-unauthenticated `
        --no-cpu-throttling `
        --quiet
}

Write-Host "Deployment finished!"
