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

$AppName = "korean-teacher-bot"
$Region = "asia-northeast1"
$BucketName = "$ProjectId-korean-teacher-data"

# Ensure Cloud Storage bucket exists
Write-Host "Verifying Cloud Storage bucket gs://$BucketName..."
python sync_secrets.py --init-bucket --project $ProjectId

Write-Host "Starting gcloud run deploy for $AppName in $Region..."
# Cloud Run automatically authenticates to Secret Manager and Cloud Storage via Application Default Credentials
gcloud run deploy $AppName `
    --source . `
    --region $Region `
    --allow-unauthenticated `
    --no-cpu-throttling `
    --set-env-vars "GOOGLE_CLOUD_PROJECT=$ProjectId,GCS_BUCKET_NAME=$BucketName" `
    --quiet

Write-Host "Deployment finished!"
