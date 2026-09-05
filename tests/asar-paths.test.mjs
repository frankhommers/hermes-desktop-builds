import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {asarName} from '../scripts/asar-paths.mjs';
test('Windows ASAR lookups use native separators, not normalized POSIX inventory keys',()=>assert.equal(asarName('dist/assets/Collapse-Bold-mgICk9-_.woff2',path.win32),'dist\\assets\\Collapse-Bold-mgICk9-_.woff2'));
test('POSIX inventory keys remain unchanged on Mac/Linux',()=>assert.equal(asarName('dist/assets/font.woff2',path.posix),'dist/assets/font.woff2'));
