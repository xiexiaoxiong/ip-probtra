import { lstat, mkdir, realpath } from 'node:fs/promises';
import path from 'node:path';
import {
  invalidityUploadFileKey,
  type InvalidityEnvironment,
} from '@/lib/invalidity-contracts';

type UploadRootEnvironment = {
  INVALIDITY_UPLOAD_ROOT?: string;
  INVALIDITY_TEST_UPLOAD_ROOT?: string;
  INVALIDITY_PROD_UPLOAD_ROOT?: string;
};

export type InvalidityUploadStorageOptions = {
  environment?: UploadRootEnvironment;
  projectRoot?: string;
  create?: boolean;
};

type ConfiguredUploadRootOptions = Pick<
  InvalidityUploadStorageOptions,
  'environment' | 'projectRoot'
> & {
  requiredEnvironment?: InvalidityEnvironment;
};

export class InvalidityUploadStorageError extends Error {
  status: number;

  constructor(message: string, status = 503) {
    super(message);
    this.name = 'InvalidityUploadStorageError';
    this.status = status;
  }
}

const ROOT_VARIABLES: Record<InvalidityEnvironment, keyof UploadRootEnvironment> = {
  test: 'INVALIDITY_TEST_UPLOAD_ROOT',
  prod: 'INVALIDITY_PROD_UPLOAD_ROOT',
};

const EXPECTED_RELATIVE_ROOTS: Record<InvalidityEnvironment, string> = {
  test: path.join('5-invalidity-search-test', '.data', 'uploads', 'test'),
  prod: path.join('5-invalidity-search-prod', '.data', 'uploads', 'prod'),
};

function defaultProjectRoot(): string {
  const cwd = path.resolve(process.cwd());
  return path.basename(cwd) === 'IP-protral' ? path.dirname(cwd) : cwd;
}

function isSameOrNested(parent: string, candidate: string): boolean {
  const relative = path.relative(parent, candidate);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function assertRootsSeparated(testRoot: string, prodRoot: string): void {
  if (isSameOrNested(testRoot, prodRoot) || isSameOrNested(prodRoot, testRoot)) {
    throw new InvalidityUploadStorageError('正式与测试上传根不能相同或互相嵌套');
  }
}

export function configuredInvalidityUploadRoots(
  options: ConfiguredUploadRootOptions = {},
): Record<InvalidityEnvironment, string> {
  const environment = options.environment || process.env;
  const projectRoot = path.resolve(options.projectRoot || defaultProjectRoot());
  const roots = {} as Record<InvalidityEnvironment, string>;

  for (const invalidityEnvironment of ['test', 'prod'] as const) {
    const variableName = ROOT_VARIABLES[invalidityEnvironment];
    const configured = String(
      invalidityEnvironment === 'test'
        ? environment.INVALIDITY_UPLOAD_ROOT || environment[variableName] || ''
        : environment[variableName] || '',
    ).trim();
    if (!configured) {
      if (!options.requiredEnvironment || options.requiredEnvironment === invalidityEnvironment) {
        throw new InvalidityUploadStorageError(
          `缺少 ${invalidityEnvironment === 'test' ? 'INVALIDITY_UPLOAD_ROOT' : variableName}，禁止使用未配置上传目录`,
        );
      }
      roots[invalidityEnvironment] = path.resolve(
        projectRoot,
        EXPECTED_RELATIVE_ROOTS[invalidityEnvironment],
      );
      continue;
    }
    roots[invalidityEnvironment] = path.resolve(projectRoot, configured);
  }

  assertRootsSeparated(roots.test, roots.prod);

  for (const invalidityEnvironment of ['test', 'prod'] as const) {
    const expected = path.resolve(projectRoot, EXPECTED_RELATIVE_ROOTS[invalidityEnvironment]);
    if (roots[invalidityEnvironment] !== expected) {
      throw new InvalidityUploadStorageError(
        `${ROOT_VARIABLES[invalidityEnvironment]} 未指向项目内指定的 ${invalidityEnvironment} 隔离上传目录`,
      );
    }
  }

  return roots;
}

async function lstatIfPresent(target: string) {
  try {
    return await lstat(target);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
}

async function assertNoSymlinkComponents(projectRoot: string, target: string): Promise<void> {
  const relative = path.relative(projectRoot, target);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new InvalidityUploadStorageError('上传根必须位于当前专利项目目录内');
  }

  let cursor = projectRoot;
  const projectStatus = await lstatIfPresent(cursor);
  if (!projectStatus?.isDirectory() || projectStatus.isSymbolicLink()) {
    throw new InvalidityUploadStorageError('专利项目根不存在、不是目录或包含符号链接');
  }

  for (const segment of relative.split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, segment);
    const status = await lstatIfPresent(cursor);
    if (!status) break;
    if (status.isSymbolicLink()) {
      throw new InvalidityUploadStorageError('上传根路径包含符号链接，已拒绝访问');
    }
    if (!status.isDirectory() && cursor !== target) {
      throw new InvalidityUploadStorageError('上传根父路径不是目录');
    }
  }
}

async function realRootIfPresent(root: string): Promise<string> {
  const status = await lstatIfPresent(root);
  if (!status) return root;
  if (!status.isDirectory() || status.isSymbolicLink()) {
    throw new InvalidityUploadStorageError('上传根不是允许的普通目录或包含符号链接');
  }
  return realpath(root);
}

export async function prepareInvalidityUploadRoot(
  invalidityEnvironment: InvalidityEnvironment,
  options: InvalidityUploadStorageOptions = {},
): Promise<string> {
  const projectRoot = path.resolve(options.projectRoot || defaultProjectRoot());
  const roots = configuredInvalidityUploadRoots({
    environment: options.environment,
    projectRoot,
    requiredEnvironment: invalidityEnvironment,
  });

  await Promise.all([
    assertNoSymlinkComponents(projectRoot, roots.test),
    assertNoSymlinkComponents(projectRoot, roots.prod),
  ]);

  const requestedRoot = roots[invalidityEnvironment];
  if (options.create) {
    await mkdir(requestedRoot, { recursive: true });
    await assertNoSymlinkComponents(projectRoot, requestedRoot);
  }

  const requestedStatus = await lstatIfPresent(requestedRoot);
  if (!requestedStatus) {
    throw new InvalidityUploadStorageError('该环境上传根不存在或尚未初始化', 404);
  }
  if (!requestedStatus.isDirectory() || requestedStatus.isSymbolicLink()) {
    throw new InvalidityUploadStorageError('该环境上传根不是允许的普通目录或包含符号链接');
  }

  const [testRealRoot, prodRealRoot] = await Promise.all([
    realRootIfPresent(roots.test),
    realRootIfPresent(roots.prod),
  ]);
  assertRootsSeparated(testRealRoot, prodRealRoot);

  const requestedRealRoot = invalidityEnvironment === 'test' ? testRealRoot : prodRealRoot;
  if (requestedRealRoot !== requestedRoot) {
    throw new InvalidityUploadStorageError('该环境上传根真实路径与配置路径不一致，已拒绝访问');
  }
  return requestedRealRoot;
}

export async function resolveInvalidityUploadPath(
  invalidityEnvironment: InvalidityEnvironment,
  fileKey: unknown,
  options: Omit<InvalidityUploadStorageOptions, 'create'> = {},
): Promise<string> {
  let key: string;
  try {
    key = invalidityUploadFileKey(fileKey, invalidityEnvironment);
  } catch (error) {
    throw new InvalidityUploadStorageError(
      error instanceof Error ? error.message : '上传 fileKey 无效',
      400,
    );
  }

  const uploadRoot = await prepareInvalidityUploadRoot(invalidityEnvironment, options);
  const candidate = path.resolve(uploadRoot, key);
  if (!isSameOrNested(uploadRoot, candidate) || candidate === uploadRoot) {
    throw new InvalidityUploadStorageError('上传 fileKey 逃逸允许目录', 400);
  }

  const status = await lstatIfPresent(candidate);
  if (!status) {
    throw new InvalidityUploadStorageError('上传文件不存在或已过期', 404);
  }
  if (status.isSymbolicLink()) {
    throw new InvalidityUploadStorageError('上传文件是符号链接，已拒绝访问', 400);
  }
  if (!status.isFile()) {
    throw new InvalidityUploadStorageError('上传目标不是允许的普通文件', 400);
  }

  const resolved = await realpath(candidate);
  if (!isSameOrNested(uploadRoot, resolved) || resolved === uploadRoot) {
    throw new InvalidityUploadStorageError('上传文件真实路径逃逸允许目录', 400);
  }
  return resolved;
}
