import { build } from 'esbuild';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

// 编辑器依赖按锁文件打包为本地资源。Docker 构建直接使用产物，无需安装 Node。
const cwd = fileURLToPath(new URL('.', import.meta.url));
const output = '../../app/smartreportgen/static/admin/template-editor.bundle';
const { metafile } = await build({
  absWorkingDir: cwd,
  entryPoints: ['guide.mjs'],
  outfile: output + '.js',
  bundle: true,
  minify: true,
  format: 'esm',
  target: 'es2022',
  metafile: true,
  banner: { js: '/* 由 scripts/template-editor 生成，依赖许可见 template-editor.bundle.LICENSES.txt。 */' },
});

// 只收集实际参与打包的依赖。同一许可正文只保留一份，并列出全部适用的包。
const packages = new Set(Object.keys(metafile.inputs).flatMap(path =>
  [...path.matchAll(/(?:^|\/)node_modules\/((?:@[^/]+\/)?[^/]+)/g)].slice(-1).map(match => match[1])
));
const licenses = new Map();
for (const name of [...packages].sort()) {
  const dir = join(cwd, 'node_modules', name);
  const files = await readdir(dir);
  const file = files.find(file => /^(?:licen[cs]e|copying)(?:\.(?:md|txt|markdown))?$/i.test(file));
  if (!file) throw new Error(`找不到 ${name} 的许可文件`);
  const license = (await readFile(join(dir, file), 'utf8')).trim();
  if (!licenses.has(license)) licenses.set(license, []);
  licenses.get(license).push(name);
}
await writeFile(join(cwd, output + '.LICENSES.txt'),
  [...licenses].map(([license, names]) => names.join(', ') + '\n\n' + license).join('\n\n----\n\n') + '\n');
