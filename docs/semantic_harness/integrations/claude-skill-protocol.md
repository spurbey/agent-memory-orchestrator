# Claude Skill Protocol

## Purpose

Define equivalent explicit harness usage for Claude-style coding agents.

## Usage Rules

Claude should call the harness at planning boundaries, after noisy tool output, and before broad edits. It should request `detail=deep` only when explaining history or when the user asks why something exists.

## Safety

Claude should not convert low-confidence cards into firm claims. It should cite harness evidence IDs when using harness guidance in a plan.
