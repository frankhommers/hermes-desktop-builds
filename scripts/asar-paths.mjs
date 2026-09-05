import path from 'node:path';
export const asarName=(name,pathApi=path)=>pathApi.normalize(name);
