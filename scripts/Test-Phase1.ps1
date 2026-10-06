param(
    [string]$BaseUrl = 'http://localhost:8080',
    [string]$UserA = 'demo_a',
    [string]$PasswordA = 'Demo-A-2026!',
    [string]$UserB = 'demo_b',
    [string]$PasswordB = 'Demo-B-2026!',
    [switch]$Recreate
)
$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')
Set-Location (Split-Path $PSScriptRoot -Parent)

function Assert-True($Condition, $Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    Write-Output "PASS: $Message"
}

function Invoke-Api($Session, $Method, $Path, $Body, $Csrf, $ExpectedStatus) {
    $headers = @{ Origin = $BaseUrl }
    if ($Csrf) { $headers['X-CSRF-Token'] = $Csrf }
    $args = @{
        Uri = "$BaseUrl/api/v1$Path"; Method = $Method
        WebSession = $Session; Headers = $headers; UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        $args['Body'] = ($Body | ConvertTo-Json -Compress)
        $args['ContentType'] = 'application/json'
    }
    try {
        $result = Invoke-WebRequest @args
        $actual = [int]$result.StatusCode
    } catch {
        if (-not $_.Exception.Response) { throw }
        $actual = [int]$_.Exception.Response.StatusCode
        if ($actual -ne $ExpectedStatus) { throw }
        $result = $null
    }
    if ($actual -ne $ExpectedStatus) { throw "$Method $Path expected $ExpectedStatus, got $actual" }
    return $result
}

$a = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$b = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-Api $a GET '/health' $null $null 200 | Out-Null
Invoke-Api $a GET '/auth/me' $null $null 401 | Out-Null
Assert-True $true 'Unauthenticated /auth/me returns 401'
Invoke-Api $a POST '/auth/login' @{username=$UserA;password='wrong-password'} $null 401 | Out-Null
Assert-True $true 'Wrong password rejected'
$loginA = (Invoke-Api $a POST '/auth/login' @{username=$UserA;password=$PasswordA} $null 200).Content | ConvertFrom-Json
$loginB = (Invoke-Api $b POST '/auth/login' @{username=$UserB;password=$PasswordB} $null 200).Content | ConvertFrom-Json
Assert-True ($loginA.user.username -eq $UserA) 'Account A logged in'
Assert-True ($loginB.user.username -eq $UserB) 'Account B logged in'
Assert-True ($loginA.user.id -ne $loginB.user.id) 'Accounts have distinct IDs'
$meA = (Invoke-Api $a GET '/auth/me' $null $null 200).Content | ConvertFrom-Json
$meB = (Invoke-Api $b GET '/auth/me' $null $null 200).Content | ConvertFrom-Json
Assert-True ($meA.user.id -eq $loginA.user.id -and $meB.user.id -eq $loginB.user.id) 'Backend returns each current identity'
Invoke-Api $a POST '/auth/logout' $null $null 403 | Out-Null
Invoke-Api $a GET '/auth/me' $null $null 200 | Out-Null
Assert-True $true 'Missing CSRF denied without revoking session'

if ($Recreate) {
    & docker compose up -d --force-recreate --wait
    if ($LASTEXITCODE -ne 0) { throw 'Container recreation failed' }
    $afterA = (Invoke-Api $a GET '/auth/me' $null $null 200).Content | ConvertFrom-Json
    $afterB = (Invoke-Api $b GET '/auth/me' $null $null 200).Content | ConvertFrom-Json
    Assert-True ($afterA.user.id -eq $loginA.user.id -and $afterB.user.id -eq $loginB.user.id) 'Accounts and active sessions survive container recreation'
}

# Keep a copy of the old cookie to prove server-side revocation, not just UI logout.
$old = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$cookie = $a.Cookies.GetCookies([Uri]$BaseUrl)['resume_session']
if (-not $cookie) { throw 'No session cookie returned' }
$old.Cookies.Add([Uri]$BaseUrl, (New-Object System.Net.Cookie('resume_session', $cookie.Value, '/')))
Invoke-Api $a POST '/auth/logout' $null $loginA.csrf_token 204 | Out-Null
Invoke-Api $a GET '/auth/me' $null $null 401 | Out-Null
Invoke-Api $old GET '/auth/me' $null $null 401 | Out-Null
Assert-True $true 'Logout invalidates the old cookie on the server'
Invoke-Api $b GET '/auth/me' $null $null 200 | Out-Null
Assert-True $true 'Logging out A leaves B authenticated'
Invoke-Api $b POST '/auth/logout' $null $loginB.csrf_token 204 | Out-Null
Write-Output 'Phase 1 host API checks completed. Browser interaction still requires manual verification.'
