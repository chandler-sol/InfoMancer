import { existsSync, readdirSync, readFileSync, renameSync } from 'node:fs';
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

// Invoke the JavaScript entrypoint with the current Node executable rather than
// relying on platform-specific .cmd/.bin shims. This keeps the wrapper identical
// on Windows, macOS, and Linux.
const result = spawnSync(process.execPath, [tauriCli, ...args], {
  cwd: desktopDir,
  stdio: 'inherit',
  shell: false,
});

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

  const matches = readdirSync(directory).filter((name) => name.endsWith(extension));
  if (matches.length === 0) return;
  if (matches.length !== 1) {
    throw new Error(
      `Expected one ${extension} bundle in ${directory}, found ${matches.length}: ${matches.join(', ')}`,
    );
  }

  const source = join(directory, matches[0]);
  const destination = join(directory, `InfoMancer-${version}-${label}${extension}`);
  const sourceSignature = `${source}.sig`;
  const destinationSignature = `${destination}.sig`;

  if (source !== destination) {
    renameSync(source, destination);
    // Signed updater builds place a signature beside the bundle. Keep the pair
    // under the same basename so tauri-action can still match them when it builds
    // latest.json and uploads updater assets.
    if (existsSync(sourceSignature)) {
      renameSync(sourceSignature, destinationSignature);
    }
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
