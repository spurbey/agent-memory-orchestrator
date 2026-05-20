[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Profile,

    [string]$Region = "ap-south-1",
    [string]$StackName = "amo-peer-relay",
    [string]$RepositoryUrl = "https://github.com/spurbey/agent-memory-orchestrator.git",
    [string]$RepositoryRef = "",
    [string]$RelayNodeId = "amo-relay-prod",
    [string]$Namespace = "amo-peer-default",
    [string]$InstanceType = "t3.micro",
    [int]$RelayPort = 4001,
    [string]$RelayIngressCidr = "0.0.0.0/0",
    [string]$VpcId = "",
    [string]$SubnetId = "",
    [string]$KeyName = "",
    [switch]$SkipSsmCheck
)

$ErrorActionPreference = "Stop"

function Invoke-AwsJson {
    param([string[]]$Arguments)
    $output = & aws @Arguments --output json
    if ($LASTEXITCODE -ne 0) {
        throw "aws command failed: aws $($Arguments -join ' ')"
    }
    if (-not $output) {
        return $null
    }
    return ($output | ConvertFrom-Json)
}

function Invoke-AwsText {
    param([string[]]$Arguments)
    $output = & aws @Arguments --output text
    if ($LASTEXITCODE -ne 0) {
        throw "aws command failed: aws $($Arguments -join ' ')"
    }
    return ($output -join "`n").Trim()
}

function Wait-ForSsmOnline {
    param(
        [string]$InstanceId,
        [string]$ProfileName,
        [string]$AwsRegion
    )
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        $status = & aws ssm describe-instance-information `
            --profile $ProfileName `
            --region $AwsRegion `
            --filters "Key=InstanceIds,Values=$InstanceId" `
            --query "InstanceInformationList[0].PingStatus" `
            --output text 2>$null
        if ($LASTEXITCODE -eq 0 -and (($status -join "").Trim()) -eq "Online") {
            return
        }
        Start-Sleep -Seconds 10
    }
    throw "SSM did not report instance online: $InstanceId"
}

function Invoke-SsmShell {
    param(
        [string]$InstanceId,
        [string]$ProfileName,
        [string]$AwsRegion,
        [string[]]$Commands
    )
    $parametersFile = [System.IO.Path]::GetTempFileName()
    try {
        $parametersJson = @{ commands = $Commands } | ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText($parametersFile, $parametersJson, [System.Text.UTF8Encoding]::new($false))
        $commandId = Invoke-AwsText -Arguments @(
            "ssm", "send-command",
            "--profile", $ProfileName,
            "--region", $AwsRegion,
            "--instance-ids", $InstanceId,
            "--document-name", "AWS-RunShellScript",
            "--parameters", "file://$parametersFile",
            "--query", "Command.CommandId"
        )
        for ($attempt = 1; $attempt -le 60; $attempt++) {
            Start-Sleep -Seconds 2
            $invocation = Invoke-AwsJson -Arguments @(
                "ssm", "get-command-invocation",
                "--profile", $ProfileName,
                "--region", $AwsRegion,
                "--command-id", $commandId,
                "--instance-id", $InstanceId
            )
            if ($invocation.Status -in @("Pending", "InProgress", "Delayed")) {
                continue
            }
            if ($invocation.Status -ne "Success") {
                throw "SSM command failed with status $($invocation.Status): $($invocation.StandardErrorContent)"
            }
            return $invocation.StandardOutputContent
        }
        throw "SSM command timed out: $commandId"
    }
    finally {
        Remove-Item -LiteralPath $parametersFile -Force -ErrorAction SilentlyContinue
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$templatePath = Join-Path $repoRoot "infra\aws\peer-relay\cloudformation.yaml"
if (-not (Test-Path $templatePath)) {
    throw "CloudFormation template not found: $templatePath"
}

if (-not $RepositoryRef) {
    $RepositoryRef = (& git -C $repoRoot rev-parse --abbrev-ref HEAD 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $RepositoryRef) {
        $RepositoryRef = "main"
    }
}

$upstream = (& git -C $repoRoot rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null | Select-Object -First 1)
if ($LASTEXITCODE -eq 0 -and $upstream) {
    $aheadText = (& git -C $repoRoot rev-list --count "$upstream..HEAD" 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -eq 0 -and [int]$aheadText -gt 0) {
        Write-Warning "Current branch is $aheadText commit(s) ahead of $upstream. Push before deploying, or pass -RepositoryRef with a pushed branch/tag/commit."
    }
}

$identity = Invoke-AwsJson -Arguments @("sts", "get-caller-identity", "--profile", $Profile, "--region", $Region)
Write-Host "Using AWS account $($identity.Account) via profile '$Profile' in $Region"

if (-not $VpcId) {
    $VpcId = Invoke-AwsText -Arguments @(
        "ec2", "describe-vpcs",
        "--profile", $Profile,
        "--region", $Region,
        "--filters", "Name=isDefault,Values=true",
        "--query", "Vpcs[0].VpcId"
    )
    if (-not $VpcId -or $VpcId -eq "None") {
        throw "No default VPC found in $Region. Pass -VpcId and -SubnetId explicitly."
    }
    Write-Host "Using default VPC $VpcId"
}

if (-not $SubnetId) {
    $SubnetId = Invoke-AwsText -Arguments @(
        "ec2", "describe-subnets",
        "--profile", $Profile,
        "--region", $Region,
        "--filters", "Name=vpc-id,Values=$VpcId", "Name=default-for-az,Values=true",
        "--query", "Subnets[0].SubnetId"
    )
    if (-not $SubnetId -or $SubnetId -eq "None") {
        throw "No default subnet found for VPC $VpcId. Pass -SubnetId explicitly."
    }
    Write-Host "Using default subnet $SubnetId"
}

$parameterOverrides = @(
    "RepositoryUrl=$RepositoryUrl",
    "RepositoryRef=$RepositoryRef",
    "RelayNodeId=$RelayNodeId",
    "RelayNamespace=$Namespace",
    "RelayPort=$RelayPort",
    "RelayIngressCidr=$RelayIngressCidr",
    "InstanceType=$InstanceType",
    "VpcId=$VpcId",
    "SubnetId=$SubnetId",
    "KeyName=$KeyName"
)

& aws cloudformation deploy `
    --profile $Profile `
    --region $Region `
    --stack-name $StackName `
    --template-file $templatePath `
    --capabilities CAPABILITY_IAM `
    --parameter-overrides $parameterOverrides
if ($LASTEXITCODE -ne 0) {
    throw "CloudFormation deploy failed"
}

$stack = Invoke-AwsJson -Arguments @("cloudformation", "describe-stacks", "--profile", $Profile, "--region", $Region, "--stack-name", $StackName)
$outputs = @{}
foreach ($item in $stack.Stacks[0].Outputs) {
    $outputs[$item.OutputKey] = $item.OutputValue
}
$instanceId = [string]$outputs.InstanceId
if (-not $instanceId) {
    throw "Stack output InstanceId missing"
}

$relayInfo = $null
if (-not $SkipSsmCheck) {
    Wait-ForSsmOnline -InstanceId $instanceId -ProfileName $Profile -AwsRegion $Region
    $relayJson = Invoke-SsmShell -InstanceId $instanceId -ProfileName $Profile -AwsRegion $Region -Commands @(
        "set -e",
        "systemctl is-active amo-peer-relay.service",
        "cat /opt/amo-relay/relay.json"
    )
    $jsonStart = $relayJson.IndexOf("{")
    if ($jsonStart -ge 0) {
        $relayInfo = $relayJson.Substring($jsonStart) | ConvertFrom-Json
    }
}

$relayMultiaddr = if ($relayInfo -and $relayInfo.relay_multiaddr) {
    [string]$relayInfo.relay_multiaddr
} else {
    "/ip4/$($outputs.PublicIp)/tcp/$RelayPort/p2p/<relay-peer-id>"
}

$result = [ordered]@{
    ok = $true
    stack_name = $StackName
    region = $Region
    aws_account = $identity.Account
    instance_id = $instanceId
    public_ip = $outputs.PublicIp
    relay_port = [int]$outputs.RelayPort
    relay_multiaddr = $relayMultiaddr
    rendezvous_addr = $relayMultiaddr
    rendezvous_namespace = [string]$outputs.RelayNamespace
    client_enable_command = "amo-cli peer enable --node-id <device-node-id> --static-relay `"$relayMultiaddr`" --auto-relay --hole-punching --rendezvous-addr `"$relayMultiaddr`" --rendezvous-namespace `"$($outputs.RelayNamespace)`""
    create_invite_command = "amo-cli peer create-invite --auto-approve --rendezvous-addr `"$relayMultiaddr`" --rendezvous-namespace `"$($outputs.RelayNamespace)`" --out host.invite.json"
    delete_command = "aws cloudformation delete-stack --profile $Profile --region $Region --stack-name $StackName"
    relay_info = $relayInfo
}

$result | ConvertTo-Json -Depth 12
