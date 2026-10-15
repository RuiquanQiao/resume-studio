// Fixture: the shape of career-ops' updater (`check` prints one JSON line).
const cmd = process.argv[2];
if (cmd === 'check') console.log(JSON.stringify({ status: 'update-available', local: '1.36.0', remote: '1.37.0', changelog: '- feat: something new' }));
else if (cmd === 'apply') console.log('Updated to 1.37.0');
else if (cmd === 'rollback') console.log('Rolled back');
else process.exit(2);
