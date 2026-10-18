import { main } from './cli.mjs';

const code = await main(process.argv.slice(2));
process.exitCode = code;
setTimeout(() => process.exit(code), 3000).unref();
