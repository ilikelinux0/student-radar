# Setup details

The README's "Set up your own" steps are all you need to get results. This page covers what the daily job does, putting the reader online so you can open it from your phone, and using Amazon Bedrock instead of Gemini.

## What the workflow does

`.github/workflows/sweep.yml` runs at 06:00 UTC every day and can be started by hand from the Actions tab. The manual run has a `force` input that checks every enabled source and ignores each one's daily or weekly wait.

It checks out the repository, installs Python 3.12 and the locked dependencies, and installs a browser only when an enabled source has `js: true`. It reads `scorer_provider` from `settings.yaml`, and logs in to AWS only when that value is `bedrock`. Then it runs `python -m radar.sweep` with your secrets.

Without the `RADAR_PROFILE` secret the sweep logs "sweep skipped: no profile" and exits. With it, the sweep writes `data/radar.db`, `web/public/data/offers.json`, and `web/public/data/sources.json`. The workflow commits those files when one of them changed and pushes `main`. The commit is authored as `github-actions[bot]`.

A source that fails is recorded on the Source health page and the run still commits. A bad config or a failed push does not update the files.

If your account overrides workflow permissions, set Settings → Actions → General → Workflow permissions to read and write. The workflow's `contents: write` permission is what lets the bot push the results.

## Your profile

`profile.yaml` at the repository root is gitignored. `git check-ignore -v profile.yaml` should name the `.gitignore` line. Never commit it, and never commit your CV.

The `RADAR_PROFILE` secret holds the same text. Edit both when something changes.

`fit_summary` is the only profile text sent to the model. The facts (birth date, citizenship, GPA, English, military service) are compared by code and are never sent, stored, exported, or logged. `private_terms` are removed from anything the model writes, in case it repeats them.

## Put the reader online (Cloudflare Pages)

The reader is a static site. Cloudflare Pages can rebuild it every time the daily job pushes. Cloudflare Access keeps it private: it sends a one-time PIN only to the email addresses you allow. Both are free.

1. In the Cloudflare dashboard, go to Workers & Pages → Create → Pages → Connect to Git.
2. Choose GitHub. Install the Cloudflare app and limit it to your private copy of this repository.
3. Select the repository. Production branch: `main`.
4. Use the React (Vite) preset. Root directory: `web`. Build command: `npm run build`. Build output directory: `dist`. Do not add your Gemini key or any other secret as a Pages variable. The page does not need them.
5. Save and deploy. The first build publishes `PROJECT.pages.dev`. **That address is public until you finish the Access steps below**, so do them right away.

Make it private with Access:

6. Open the Pages project → Settings → General, and select Enable access policy. This protects preview URLs only, not `PROJECT.pages.dev`.
7. Select Manage on the policy that was just created. Under Access → Applications, open the application and select Configure. Under Public hostname, delete the wildcard (`*`) in the Subdomain field and save. If saving reports a conflict, change the application name. Now `PROJECT.pages.dev` is behind Access.
8. Leave preview deployments off (Pages project → Settings), so a branch push cannot publish a second URL.
9. Open the application's policy and make it one Allow rule. Include selector: Emails. Add only your own address. Do not choose Everyone and do not add a Bypass rule.
10. Leave the login method as One-time PIN. The code comes from `noreply@notify.cloudflare.com` and expires after 10 minutes.

Open the site in a private window to check: you should see the PIN page, and an address that is not on the list should get no PIN.

Marks you set on the page (interested, applied, interview, and the rest) are stored in that browser. Export them from the sidebar to move them to another device.

### If `pages.dev` is blocked on your network

Some networks close the connection to `*.pages.dev`. You can serve the same site from your `workers.dev` address with the Worker in `worker-personal/`:

1. In Zero Trust → Access → Service credentials, create a service token. Add a second policy to the Pages application from step 9: Service Auth, that token.
2. In `worker-personal/index.js`, set `ORIGIN` to your Pages address.
3. From `worker-personal/`, run `npx wrangler login`, then `npx wrangler deploy`. Set the token with `npx wrangler secret put ACCESS_CLIENT_ID` and `npx wrangler secret put ACCESS_CLIENT_SECRET` (paste only the value, without the `CF-Access-Client-Id:` label).
4. Create a separate Access application for the Worker's `workers.dev` hostname, with the same one-address Allow rule. Keep the two hostnames in separate applications. When one application holds both, the login can finish on the `pages.dev` hostname, which is the one your network blocks.

## Optional: Amazon Bedrock instead of Gemini

Set `scorer_provider: bedrock` in `settings.yaml` and add the secrets `AWS_ROLE_ARN`, `AWS_REGION`, and `BEDROCK_MODEL_ID`. The workflow logs in with GitHub's OpenID Connect, so no AWS key is stored. Placeholders below are in capitals. Copy every value from the AWS console; do not guess one.

1. In IAM, add an OpenID Connect identity provider. Provider URL: `https://token.actions.githubusercontent.com`. Audience: `sts.amazonaws.com`.
2. Create a role that trusts that provider only for your repository's `main` branch:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:OWNER/REPO:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

3. Give the role one permission: `bedrock:InvokeModel` on the model you enable. The foundation-model ARN has an empty account field; that is the AWS shape. If the console shows an inference-profile ARN for your model, add it as a second `Resource`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:REGION::foundation-model/MODEL_ID"
    }
  ]
}
```

4. In the Bedrock console for `REGION`, enable model access for that model. Put the role ARN in `AWS_ROLE_ARN`, the region in `AWS_REGION`, and the model id in `BEDROCK_MODEL_ID`.
