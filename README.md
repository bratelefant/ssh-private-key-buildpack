# ssh-private-key-buildpack

A Buildpack for setting the ssh private key as part of the application build. It's meant to be used as part of a setup [using multiple buildpacks](https://doc.scalingo.com/platform/deployment/buildpacks/multi).

# Example usage

## Configure Multiple Buildpacks

The buildpack should be configured using a `.buildpacks` in combination with the [`multi-buildpack`](https://doc.scalingo.com/platform/deployment/buildpacks/multi)

    $ scalingo --app my-app env-set BUILDPACK_URL=https://github.com/Scalingo/multi-buildpack.git

The same example given for the CLI use would have the following `.buildpacks` file.

    $ cat .buildpacks
    https://github.com/Scalingo/ssh-private-key-buildpack.git
    https://github.com/Scalingo/nodejs-buildpack.git

## Configure SSH Key

Set the private key environment variable `SSH_KEY` of your app (note that the key needs to be base64 encoded).

    $ scalingo --app my-app env-set SSH_KEY="$(cat path/to/your/keys/id_rsa | base64)"

By default the buildpack adds GitHub to `known_hosts`. However you can configure your app to allow custom hosts, too. All that's needed is the set `SSH_HOSTS` for you app to a comma-separated list of hosts, e.g. `git@github.com,example.com`

    $ scalingo --app my-app env-set SSH_HOSTS="git@github.com,example.com"

## SSH Key Management Strategy

Because the buildpack cannot provide a password, the provided SSH key cannot be encrypted. Thus our advice to fetch private GitHub repository is to setup a GitHub Deploy Key, unencrypted and read-only only for the target repositories you want to fetch.

## Additional repository-specific deploy keys

The original `SSH_KEY` and `SSH_HOSTS` behavior is unchanged. This fork also
accepts optional pairs of build environment variables:

```text
SSH_KEY_CLIENT_SDK=<Base64-encoded private key>
SSH_KEY_CLIENT_SDK_REPOSITORY=github.com/bratelefant/planb-client-sdk.git
```

Choose any uppercase name after `SSH_KEY_`; every additional key needs its
matching `_REPOSITORY` variable. Use an unencrypted, read-only deploy key for
that repository. Do not include a URL scheme or username in `_REPOSITORY`.

The buildpack configures an SSH host alias for each additional key and rewrites
only the matching SSH, SCP-style and HTTPS Git URLs. Package manifests and
lockfiles do not need to change. It verifies repository read access before the
next buildpack runs. A missing or invalid configured key fails the build early.

An absolute `GIT_CONFIG_GLOBAL` is exported to subsequent buildpacks, so the
mapping also applies when Meteor changes `HOME` during its build. Existing Git
configuration, unrelated repository URLs and the original `SSH_KEY` are kept.
Additional private keys live in a temporary directory outside the app image.

Local offline regression checks (Python 3, Bash, Git and OpenSSH):

```sh
python3 tests/additional_keys_test.py
```
