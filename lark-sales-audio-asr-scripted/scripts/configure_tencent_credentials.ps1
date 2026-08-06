$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "Use a newly rotated Tencent Cloud API key."
Write-Host "The credentials will not be written to Skill config or logs."
$secretId = Read-Host "Tencent SecretId"
$secureSecretKey = Read-Host "Tencent SecretKey (input is hidden)" -AsSecureString

if ([string]::IsNullOrWhiteSpace($secretId)) {
    throw "SecretId cannot be empty."
}

$secretKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecretKey)
try {
    $secretKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretKeyPointer)
    if ([string]::IsNullOrWhiteSpace($secretKey)) {
        throw "SecretKey cannot be empty."
    }
    [Environment]::SetEnvironmentVariable("TENCENT_ASR_SECRET_ID", $secretId.Trim(), "User")
    [Environment]::SetEnvironmentVariable("TENCENT_ASR_SECRET_KEY", $secretKey, "User")
}
finally {
    if ($secretKeyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretKeyPointer)
    }
    Remove-Variable secretKey -ErrorAction SilentlyContinue
}

Write-Host "Tencent ASR user environment variables are configured."
Write-Host "SecretKey was not displayed."
