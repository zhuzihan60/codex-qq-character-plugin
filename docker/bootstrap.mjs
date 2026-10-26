// Initialization only. Never starts a model turn, reads auth.json or copies host data.
import fs from 'node:fs/promises';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {diagnosticConnection} from './bootstrap-protocol.mjs';

// Only fixed deployment profiles can choose paths/capabilities. No QQ input is read.
const profiles = {
  chat: {home:'/home/character/.codex', workspace:'/workspace/character', skills:['character','character-memory']},
  development: {home:'/home/character/.codex-dev', workspace:'/workspace/development', skills:['character-development']},
};
const profile = profiles[process.env.QQ_CHARACTER_PROFILE || 'chat'];
if (!profile || (process.env.CODEX_HOME && process.env.CODEX_HOME !== profile.home)) {
  throw new Error('Invalid character deployment profile or home');
}
const characterHome = profile.home;
const workspace = profile.workspace;
const allowed = new Set(profile.skills);
const fixed = `approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
cli_auth_credentials_store = "file"
[features]
shell_tool = false
shell_snapshot = false
apps = false
hooks = false
multi_agent = false
memories = false
goals = false
remote_plugin = false
skill_mcp_dependency_install = false
[tools.update_plan]
enabled = false
[tools.experimental_request_user_input]
enabled = false
`;

await fs.mkdir(characterHome, {recursive:true});
await fs.rm(path.join(characterHome, 'character-ready'), {force:true});
await fs.cp('/opt/character-seed/skills', path.join(characterHome, 'skills'), {recursive:true});
// Dedicated volume only: operator-managed character settings, never the normal home.
await fs.writeFile(path.join(characterHome, 'config.toml'), fixed, {mode:0o600});
const child = spawn('codex', ['app-server', '--listen', 'stdio://'], {
  cwd:workspace, stdio:['pipe', 'pipe', 'ignore'], env:{...process.env, CODEX_HOME:characterHome}
});
const connection = diagnosticConnection(child);
try {
  await connection.request('initialize',{clientInfo:{name:'character_bootstrap',version:'0.1.0'},capabilities:{experimentalApi:true}});
  connection.notify('initialized',{});
  const listed = await connection.request('skills/list',{cwds:[workspace],forceReload:true});
  const all = listed.data.flatMap(entry => {
    if (entry.errors.length) throw new Error('Skill discovery has errors');
    return entry.skills;
  });
  let text = fixed;
  for (const skill of all) {
    // Use the actual path returned by this installed CLI. Do not guess .system names.
    text += `\n[[skills.config]]\npath = ${JSON.stringify(skill.path)}\nenabled = ${allowed.has(skill.name)}\n`;
  }
  await fs.writeFile(path.join(characterHome,'config.toml'),text,{mode:0o600});
  await fs.writeFile(path.join(characterHome,'character-ready'),'bootstrap-complete; live preflight still required\n',{mode:0o600});
  console.log('Character home initialized; no inference was performed.');
} catch (error) {
  console.error('Character bootstrap failed: ' + error.message);
  process.exitCode = 1;
} finally {
  connection.close();
}
if (!process.exitCode) {
  const keepAlive = setInterval(() => {}, 3600000);
  for (const signal of ['SIGTERM','SIGINT']) process.on(signal,() => {clearInterval(keepAlive);process.exit(0);});
}
