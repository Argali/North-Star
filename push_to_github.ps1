# North Star — Push to GitHub
# Run this script from PowerShell in the North Star folder.
# You will need a GitHub Personal Access Token (PAT) with repo scope.
# Generate one at: https://github.com/settings/tokens

$ErrorActionPreference = "Stop"
$repoPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoPath

Write-Host "=== North Star — GitHub Push ===" -ForegroundColor Cyan
Write-Host "Working in: $repoPath"

# Step 1: Clean up corrupted .git if present
if (Test-Path ".git") {
    Write-Host "`nRemoving existing .git folder..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force ".git"
    Write-Host "Done." -ForegroundColor Green
}

# Step 2: Initialize fresh repo
Write-Host "`nInitializing git repository..." -ForegroundColor Yellow
git init -b main
git config user.name "Argali"
git config user.email "erwankervazo@gmail.com"
Write-Host "Done." -ForegroundColor Green

# Step 3: Stage everything
Write-Host "`nStaging all files..." -ForegroundColor Yellow
git add -A
git status --short
Write-Host "Done." -ForegroundColor Green

# Step 4: Initial commit
Write-Host "`nCreating initial commit..." -ForegroundColor Yellow
git commit -m "Initial commit — North Star architecture specification

- Foundation: philosophy, principles, glossary
- Architecture: data models, knowledge lifecycle, memory model
- Agents: Analyst, Archivist, QA, Scribe with system prompts
- Storage: SQLite schema, knowledge graph, retention, vector store
- Operations: workflows, governance, auditing, observability
- Examples: Fleet Manager, Webfleet integration, Project Alpha
- Roadmap: phases, future agents, open research questions
- Reference templates and JSON validation schemas
- MIT License"
Write-Host "Done." -ForegroundColor Green

# Step 5: Add remote
Write-Host "`nAdding GitHub remote..." -ForegroundColor Yellow
git remote add origin https://github.com/Argali/North-Star.git
Write-Host "Done." -ForegroundColor Green

# Step 6: Push
Write-Host "`nPushing to GitHub..." -ForegroundColor Yellow
Write-Host "You will be prompted for your GitHub credentials." -ForegroundColor Cyan
Write-Host "Username: your GitHub username (Argali)" -ForegroundColor Cyan
Write-Host "Password: use a Personal Access Token, NOT your password" -ForegroundColor Cyan
Write-Host "Generate a token at: https://github.com/settings/tokens (scope: repo)" -ForegroundColor Cyan
Write-Host ""
git push -u origin main

Write-Host "`n=== Done! ===" -ForegroundColor Green
Write-Host "Repo: https://github.com/Argali/North-Star" -ForegroundColor Cyan
