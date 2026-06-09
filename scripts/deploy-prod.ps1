param(
    [ValidateSet("all", "backend", "frontend")]
    [string]$Service = "all",

    [string]$ServerHost = "47.100.65.191",
    [string]$ServerUser = "root",
    [string]$RemoteDir = "/opt/acgagent",
    [string]$PublicBaseUrl = "http://47.100.65.191",
    [string]$FrontendBind = "127.0.0.1:8080",
    [string]$HealthUrl = "http://47.100.65.191/api/v1/health",

    [switch]$SkipBuild,
    [switch]$IncludeNeo4j,
    [switch]$SkipHealthCheck
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [string]$Title,
        [scriptblock]$Script
    )

    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
    & $Script
}

function Invoke-CheckedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $FilePath $($Arguments -join ' ')"
    }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$remote = "${ServerUser}@${ServerHost}"
$archivePath = Join-Path $env:TEMP "acgagent-images-$Service.tar"

$services = switch ($Service) {
    "backend" { @("backend") }
    "frontend" { @("frontend") }
    default { @("backend", "frontend") }
}

$images = switch ($Service) {
    "backend" { @("acgagent-backend:latest") }
    "frontend" { @("acgagent-frontend:latest") }
    default { @("acgagent-backend:latest", "acgagent-frontend:latest") }
}

if ($IncludeNeo4j) {
    $images += "neo4j:5.20-community"
}

Push-Location $repoRoot
try {
    Invoke-Step "Check required tools" {
        foreach ($tool in @("docker", "ssh", "scp")) {
            if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
                throw "Required tool not found in PATH: $tool"
            }
        }
        Invoke-CheckedCommand "docker" @("version")
        Invoke-CheckedCommand "docker" @("compose", "version")
    }

    if (-not $SkipBuild) {
        Invoke-Step "Build production image(s): $($services -join ', ')" {
            Invoke-CheckedCommand "docker" (@("compose", "-f", "docker-compose.prod.yml", "build") + $services)
        }
    }

    Invoke-Step "Save image archive" {
        if (Test-Path $archivePath) {
            Remove-Item -LiteralPath $archivePath -Force
        }
        Invoke-CheckedCommand "docker" (@("save") + $images + @("-o", $archivePath))
        $sizeMb = [Math]::Round((Get-Item -LiteralPath $archivePath).Length / 1MB, 1)
        Write-Host "Archive: $archivePath ($sizeMb MB)"
    }

    Invoke-Step "Prepare remote directory" {
        Invoke-CheckedCommand "ssh" @($remote, "mkdir -p '$RemoteDir' '$RemoteDir/output' '$RemoteDir/data' '$RemoteDir/bg_knowledge'")
    }

    Invoke-Step "Sync production compose file" {
        Invoke-CheckedCommand "scp" @("docker-compose.prod.yml", "${remote}:${RemoteDir}/docker-compose.prod.yml")
    }

    Invoke-Step "Sync image archive" {
        Invoke-CheckedCommand "scp" @($archivePath, "${remote}:/tmp/acgagent-images-$Service.tar")
    }

    Invoke-Step "Load image(s), refresh remote env defaults, restart service(s)" {
        $remoteScript = @"
set -eu
cd '$RemoteDir'
touch .env
set_env() {
  key="`$1"
  value="`$2"
  if grep -q "^`$key=" .env; then
    sed -i "s|^`$key=.*|`$key=`$value|" .env
  else
    printf "\n%s=%s\n" "`$key" "`$value" >> .env
  fi
}
set_env APP_ENV production
set_env APP_CORS_ORIGINS '$PublicBaseUrl'
set_env VITE_API_BASE_URL /api/v1
set_env FRONTEND_PORT_HOST '$FrontendBind'
docker load -i '/tmp/acgagent-images-$Service.tar'
docker compose -f docker-compose.prod.yml --env-file .env up -d --no-build $($services -join ' ')
docker compose -f docker-compose.prod.yml ps
"@
        $remoteScript = $remoteScript -replace "`r`n", "`n"
        $remoteScript | ssh $remote "bash -s"
        if ($LASTEXITCODE -ne 0) {
            throw "Remote deploy failed."
        }
    }

    if (-not $SkipHealthCheck) {
        Invoke-Step "Health check: $HealthUrl" {
            Invoke-CheckedCommand "curl.exe" @("--noproxy", "*", "-fsS", "--max-time", "20", $HealthUrl)
            Write-Host ""
        }
    }

    Write-Host ""
    Write-Host "Deployment finished: $PublicBaseUrl" -ForegroundColor Green
}
finally {
    Pop-Location
}
