#!/usr/bin/env bash
# Explicit, network-enabled bootstrap. Normal run/verify commands do not install tools.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)-$(uname -m)" != Darwin-arm64 ]]; then
  echo 'Install Java 25, Node 24, pnpm 11.5.0, Maven 3.9.11, ffprobe and Docker for your platform.' >&2
  exit 1
fi
mkdir -p .tools/downloads .cache
fetch() {
  local url="$1" archive="$2"
  [[ -f "$archive" ]] || curl --fail --location --retry 2 "$url" --output "$archive"
}
fetch 'https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.5%2B8/OpenJDK25U-jdk_aarch64_mac_hotspot_25.0.5_8.tar.gz' .tools/downloads/jdk.tar.gz
fetch 'https://nodejs.org/dist/v24.22.2/node-v24.22.2-darwin-arm64.tar.gz' .tools/downloads/node.tar.gz
fetch 'https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.11/apache-maven-3.9.11-bin.tar.gz' .tools/downloads/maven.tar.gz
python3 - <<'PY'
import hashlib, pathlib, tarfile
root = pathlib.Path('.tools')
packages = [
    ('jdk', 'sha256', '36dc14d797aaff6d89aab302cd053fdba6207dfdcf772052fa7b023a94b0e857'),
    ('node', 'sha256', 'aa9549349e47dffaa42dcf95d6e06ade8c4bdafc980979883b88767670df385a'),
    ('maven', 'sha512', 'bcfe4fe305c962ace56ac7b5fc7a08b87d5abd8b7e89027ab251069faebee516b0ded8961445d6d91ec1985dfe30f8153268843c89aa392733d1a3ec956c9978'),
]
for name, algorithm, expected in packages:
    archive = root / 'downloads' / (name + '.tar.gz')
    if hashlib.new(algorithm, archive.read_bytes()).hexdigest() != expected:
        raise SystemExit('Checksum mismatch: ' + str(archive))
    destination = root / name
    destination.mkdir(exist_ok=True)
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            parts = pathlib.PurePosixPath(member.name).parts[1:]
            if not parts:
                continue
            member.name = str(pathlib.PurePosixPath(*parts))
            bundle.extract(member, destination, filter='data')
    print('Verified and installed', name)
PY
source scripts/env.sh
command -v ffprobe >/dev/null || { echo 'ffprobe is required; install FFmpeg first.' >&2; exit 1; }
command -v pnpm >/dev/null || { echo 'pnpm 11.5.0 is required.' >&2; exit 1; }
pnpm --dir web install --frozen-lockfile
server/mvnw -f server/pom.xml -DskipTests dependency:go-offline
docker compose pull db
echo 'Local tools and dependencies are ready. Run scripts/package.sh to build.'
