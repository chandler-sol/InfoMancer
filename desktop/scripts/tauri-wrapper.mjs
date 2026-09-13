import { existsSync, readdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(scriptDir, '..');
const args = process.argv.slice(2);
const tauriCli = join(desktopDir, 'node_modules', '@tauri-apps', 'cli', 'tauri.js');

if (!existsSync(tauriCli)) {
  console.error('Tauri CLI is not installed. Run npm ci in desktop/ first.');
  process.exit(1);
}

function configureUpdaterPublicKeyForBuild() {
  if (args[0] !== 'build') return null;

  const publicKey = (process.env.INFOMANCER_UPDATER_PUBLIC_KEY ?? '').trim();
  if (!publicKey) return null;

  const decoded = Buffer.from(publicKey, 'base64').toString('utf8');
  if (!decoded.startsWith('untrusted comment: minisign public key:') || !decoded.includes('\nRW')) {
    throw new Error(
      'INFOMANCER_UPDATER_PUBLIC_KEY is not a Tauri-generated updater public key.',
    );
  }

  const configIndex = args.indexOf('--config');
  if (configIndex < 0 || !args[configIndex + 1]) {
    return null;
  }

  const sourceConfigPath = resolve(desktopDir, args[configIndex + 1]);
  const sourceConfig = JSON.parse(readFileSync(sourceConfigPath, 'utf8'));
  sourceConfig.plugins ??= {};
  sourceConfig.plugins.updater ??= {};
  sourceConfig.plugins.updater.pubkey = publicKey;

  const effectiveConfigPath = join(
    dirname(sourceConfigPath),
    '.infomancer-tauri-release.effective.json',
  );
  writeFileSync(effectiveConfigPath, `${JSON.stringify(sourceConfig, null, 2)}\n`, 'utf8');
  args[configIndex + 1] = effectiveConfigPath;
  console.log('Configured updater public key in the effective Tauri release config.');
  return effectiveConfigPath;
}

let effectiveConfigPath = null;
try {
  effectiveConfigPath = configureUpdaterPublicKeyForBuild();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

// Invoke the JavaScript entrypoint with the current Node executable rather than
// relying on platform-specific .cmd/.bin shims. This keeps the wrapper identical
// on Windows, macOS, and Linux.
const result = spawnSync(process.execPath, [tauriCli, ...args], {
  cwd: desktopDir,
  stdio: 'inherit',
  shell: false,
});

if (effectiveConfigPath && existsSync(effectiveConfigPath)) {
  unlinkSync(effectiveConfigPath);
}

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

// Keep ordinary Tauri commands untouched. Friendly filenames matter only for
// completed native build bundles that may be published or handed to testers.
if (args[0] !== 'build') {
  process.exit(0);
}

const { version } = JSON.parse(
  readFileSync(join(desktopDir, 'package.json'), 'utf8'),
);

function platformLabel() {
  if (process.platform === 'win32' && process.arch === 'x64') {
    return 'Windows-x64-Setup';
  }
  if (process.platform === 'darwin' && process.arch === 'arm64') {
    return 'macOS-Apple-Silicon';
  }
  if (process.platform === 'darwin' && process.arch === 'x64') {
    return 'macOS-Intel';
  }
  if (process.platform === 'linux' && process.arch === 'x64') {
    return 'Linux-x86_64';
  }
  return `${process.platform}-${process.arch}`;
}

const label = platformLabel();
const bundleRoot = join(desktopDir, 'src-tauri', 'target', 'release', 'bundle');

function renameSingleBundle(subdirectory, extension) {
  const directory = join(bundleRoot, subdirectory);
  if (!existsSync(directory)) return;

  const destinationName = `InfoMancer-${version}-${label}${extension}`;
  const destination = join(directory, destinationName);
  const destinationSignature = `${destination}.sig`;

  // The Rust target directory is cached between CI runs. That cache can contain
  // a previously renamed release package beside Tauri's newly generated bundle.
  // Do not count the canonical cached package as another build candidate.
  const matches = readdirSync(directory).filter(
    (name) => name.endsWith(extension) && name !== destinationName,
  );

  if (matches.length === 0) {
    if (existsSync(destination)) {
      console.log(`Release package: ${destination}`);
      if (existsSync(destinationSignature)) {
        console.log(`Release signature: ${destinationSignature}`);
      }
    }
    return;
  }
  if (matches.length !== 1) {
    throw new Error(
      `Expected one new ${extension} bundle in ${directory}, found ${matches.length}: ${matches.join(', ')}`,
    );
  }

  const source = join(directory, matches[0]);
  const sourceSignature = `${source}.sig`;

  // Replace stale canonical output restored from the build cache with the bundle
  // produced by this run.
  if (existsSync(destination)) {
    unlinkSync(destination);
  }
  if (existsSync(destinationSignature)) {
    unlinkSync(destinationSignature);
  }

  renameSync(source, destination);
  // Signed updater builds place a signature beside the bundle. Keep the pair
  // under the same basename so tauri-action can still match them when it builds
  // latest.json and uploads updater assets.
  if (existsSync(sourceSignature)) {
    renameSync(sourceSignature, destinationSignature);
  }

  console.log(`Release package: ${destination}`);
  if (existsSync(destinationSignature)) {
    console.log(`Release signature: ${destinationSignature}`);
  }
}

if (process.platform === 'win32') {
  renameSingleBundle('nsis', '.exe');
} else if (process.platform === 'darwin') {
  renameSingleBundle('dmg', '.dmg');
} else if (process.platform === 'linux') {
  renameSingleBundle('deb', '.deb');
  renameSingleBundle('appimage', '.AppImage');
}
