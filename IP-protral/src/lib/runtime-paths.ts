import path from 'path';

const DEFAULT_DATA_DIR = path.join(process.cwd(), '.data');

export function getDataRoot(): string {
  return process.env.LOCAL_DATA_DIR
    ? path.resolve(process.env.LOCAL_DATA_DIR)
    : DEFAULT_DATA_DIR;
}

export function getUploadsDir(): string {
  return path.join(getDataRoot(), 'uploads');
}

export function getSessionsDir(): string {
  return path.join(getDataRoot(), 'analysis-sessions');
}
