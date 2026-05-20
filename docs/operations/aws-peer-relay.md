# AWS Peer Relay Deployment

This deploys the minimum server AMO needs for cross-network peer communication: one public `amo-peer-netd` helper that provides libp2p circuit relay and rendezvous. It does not store AMO memory, room transcripts, raw evidence, summaries, or LLM prompts.

## What The Server Does

- Runs `amo-peer-netd` on a small Amazon Linux 2023 EC2 instance.
- Opens one public TCP port, default `4001`, for libp2p relay/rendezvous traffic.
- Uses an Elastic IP so the relay address stays stable.
- Uses a persisted libp2p identity key so the relay peer id stays stable after reboot.
- Uses AWS Systems Manager for health checks, so SSH is not required.

## One-Time AWS Profile Setup

Do not paste AWS root credentials into chat or repo files. Configure a separate local AWS CLI profile for the new AWS account:

```powershell
aws configure sso --profile amo-relay
```

If the account uses access keys instead of SSO:

```powershell
aws configure --profile amo-relay
```

Verify the profile points to the new account, not Dora infrastructure:

```powershell
aws sts get-caller-identity --profile amo-relay
```

## Deploy

The deployment script creates the CloudFormation stack, waits for the EC2 instance to appear in SSM, reads `/opt/amo-relay/relay.json`, and prints the exact client commands.

```powershell
.\scripts\deploy_amo_peer_relay_aws.ps1 `
  -Profile amo-relay `
  -Region ap-south-1 `
  -StackName amo-peer-relay `
  -Namespace amo-test
```

If the current branch has unpushed commits, push the branch before deploying or pass `-RepositoryRef` with a pushed branch/tag/commit. The EC2 instance clones the repository from GitHub, so it cannot see local-only commits.

Optional private-beta hardening:

```powershell
.\scripts\deploy_amo_peer_relay_aws.ps1 `
  -Profile amo-relay `
  -Region ap-south-1 `
  -RelayIngressCidr <your-public-ip>/32
```

## Client Flow After Deploy

Use the script output. The important part is that every participating device starts peer netd with the same relay and rendezvous namespace before creating or accepting invites.

```powershell
amo-cli peer enable `
  --node-id <device-node-id> `
  --static-relay "<relay_multiaddr>" `
  --auto-relay `
  --hole-punching `
  --rendezvous-addr "<relay_multiaddr>" `
  --rendezvous-namespace "<namespace>"
```

Then create the invite:

```powershell
amo-cli peer create-invite `
  --auto-approve `
  --rendezvous-addr "<relay_multiaddr>" `
  --rendezvous-namespace "<namespace>" `
  --out host.invite.json
```

The accepting device runs:

```powershell
amo-cli peer enable `
  --node-id <friend-device-node-id> `
  --static-relay "<relay_multiaddr>" `
  --auto-relay `
  --hole-punching `
  --rendezvous-addr "<relay_multiaddr>" `
  --rendezvous-namespace "<namespace>"

amo-cli peer accept-invite --file .\host.invite.json
```

With the relay reachable, `accept-invite` can send the join request back to the initiator over libp2p instead of requiring a manually returned `.card.json`.

## Check Server Health

```powershell
aws ssm send-command `
  --profile amo-relay `
  --region ap-south-1 `
  --instance-ids <instance-id> `
  --document-name AWS-RunShellScript `
  --parameters commands='["systemctl status amo-peer-relay.service --no-pager","cat /opt/amo-relay/relay.json"]'
```

## Delete

The relay is a real AWS server and can incur EC2 and Elastic IP charges. Delete it when the test is finished:

```powershell
aws cloudformation delete-stack `
  --profile amo-relay `
  --region ap-south-1 `
  --stack-name amo-peer-relay
```

## Production Notes

- Start with one helper for beta testing; run at least two helper nodes before relying on it for production.
- Narrow `RelayIngressCidr` where possible.
- Keep relay/rendezvous separate from AMO memory. It should move signed transport envelopes only.
- Add bandwidth and abuse controls before public release.

## References

- AWS CloudFormation `Fn::Base64` for EC2 user data: https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/intrinsic-function-reference-base64.html
- AWS public SSM parameters for latest Amazon Linux AMIs: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/finding-an-ami-parameter-store.html
- AWS Systems Manager Run Command: https://docs.aws.amazon.com/systems-manager/latest/userguide/send-commands-multiple.html
- libp2p circuit relay: https://libp2p.io/docs/circuit-relay/
- libp2p rendezvous: https://libp2p.io/docs/rendezvous/
