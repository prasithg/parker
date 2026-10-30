'use strict';

const childProcess = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

function extractedPageScripts(page) {
  const backendRoot = path.resolve(__dirname, '..', '..');
  const venvPython = path.join(backendRoot, '.venv', 'bin', 'python');
  const python = fs.existsSync(venvPython) ? venvPython : 'python3';
  const source = page === 'companion'
    ? 'from app.parker.companion_ui import COMPANION_PAGE_HTML as html'
    : 'from app.parker.converse_ui import CONVERSE_PAGE_HTML as html';
  const program = [
    'import json, re',
    source,
    "scripts = [s for s in re.findall(r'<script(?:\\s[^>]*)?>(.*?)</script>', html, re.S) if s.strip()]",
    'print(json.dumps(scripts))',
  ].join('\n');
  const result = childProcess.spawnSync(python, ['-c', program], {
    cwd: backendRoot,
    encoding: 'utf8',
  });
  if (result.status !== 0) {
    throw new Error(`could not extract ${page} page scripts:\n${result.stderr || result.stdout}`);
  }
  const scripts = JSON.parse(result.stdout);
  if (!scripts.length) throw new Error(`no inline scripts found in the ${page} page`);

  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), `parker-${page}-spec-`));
  const paths = scripts.map((script, index) => {
    const scriptPath = path.join(tempDir, `${page}-${index}.js`);
    fs.writeFileSync(scriptPath, script);
    return scriptPath;
  });
  process.on('exit', () => fs.rmSync(tempDir, { recursive: true, force: true }));
  return paths;
}

module.exports = { extractedPageScripts };
