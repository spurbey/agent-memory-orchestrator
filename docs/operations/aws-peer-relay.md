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

Relay deployment is private-operator work. The private peer runtime repo contains the CloudFormation template and deployment script. The script creates the stack, waits for the EC2 instance to appear in SSM, reads `/opt/amo-relay/relay.json`, and prints the exact relay multiaddr.

Run the private runtime repo deployment command with the relay AWS profile, target region, stack name, and rendezvous namespace. The command output must include the stable relay multiaddr and namespace used to sign `peer-relay-bootstrap.json`.

Run this from the private runtime repository, not the public AMO repository. The EC2 instance should fetch private peer-netd release/source using private deployment credentials; do not expose the Go source or AWS deployment templates through public AMO package data.

Optional private-beta hardening:

Use the private deployment command's ingress option to narrow relay access where possible.

## Client Flow After Deploy

Normal users should not paste relay flags, choose node ids, or install Go. Publish the relay profile through the signed AMO bootstrap metadata, then use the product commands:

```powershell
npx -y agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --with-peer
amo-cli peer setup
amo-cli peer invite
```

The accepting device uses the same install path and joins from the invite code:

```powershell
npx -y agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --with-peer
amo-cli peer join
```

`peer setup` prompts for a display name, generates a stable internal node id, installs/verifies the signed sidecar, loads the managed relay profile, starts peer networking, and installs startup entries for both `amo-peer-netd` and `peer-agent watch` by default.

`peer invite` prints a shareable `amo-peer-invite:...` code with expiry, trust notes, and friend instructions. The invite carries relay hints so `peer join` can configure the accepting device without a manually returned `.card.json`.

After both devices have completed setup/join, normal use is only:

```powershell
amo-cli peer-agent ask --query "<question>"
```

The asking bot creates the room, sends context requests, waits for peer-agent responses, and synthesizes the answer. The other device only needs its OS session and AMO startup watcher running.

## Managed Relay Bootstrap

The public AMO package should get the default relay profile from signed bootstrap metadata, not from user-entered flags. The bootstrap profile should contain:

```json
{
  "relay_profiles": [
    {
      "name": "amo-managed",
      "relay_addr": "<relay_multiaddr>",
      "rendezvous_addr": "<relay_multiaddr>",
      "rendezvous_namespace": "<namespace>",
      "auto_relay": true,
      "hole_punching": true
    }
  ],
  "signature": {
    "algorithm": "ed25519",
    "value": "<signature>"
  }
}
```

Pin the verification public key in AMO. Rotate relay hosts by publishing a new signed `peer-relay-bootstrap.json` document to the public AMO release; do not ask users to edit relay flags.

Advanced/debug equivalent without managed bootstrap:

```powershell
amo-cli peer enable `
  --node-id <device-node-id> `
  --static-relay "<relay_multiaddr>" `
  --auto-relay `
  --hole-punching `
  --rendezvous-addr "<relay_multiaddr>" `
  --rendezvous-namespace "<namespace>"
```

Keep these flags in operator/debug docs only. They are not the public user flow.

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
- Keep AWS relay deployment scripts, relay runbooks, and `peer-netd` source in the private runtime repo/package.
- The public package should install only verified Windows/macOS sidecar artifacts and should fail as `peer_sidecar_unavailable` when the artifact is unavailable.

## References

- AWS CloudFormation `Fn::Base64` for EC2 user data: https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/intrinsic-function-reference-base64.html
- AWS public SSM parameters for latest Amazon Linux AMIs: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/finding-an-ami-parameter-store.html
- AWS Systems Manager Run Command: https://docs.aws.amazon.com/systems-manager/latest/userguide/send-commands-multiple.html
- libp2p circuit relay: https://libp2p.io/docs/circuit-relay/
- libp2p rendezvous: https://libp2p.io/docs/rendezvous/
