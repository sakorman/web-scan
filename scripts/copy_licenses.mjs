import {cpSync,mkdirSync} from 'node:fs';
mkdirSync('dist/licenses',{recursive:true});
cpSync('licenses','dist/licenses',{recursive:true});
