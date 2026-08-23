import type { QueryResultRow } from 'pg';
import { ensureDatabaseReady } from '@/lib/db-init';
import { pgQuery } from '@/lib/postgres';
import { isInvalidityTestResourceId } from '@/lib/invalidity-test-proxy-policy';

export type InvalidityTestResourceKind = 'investigation' | 'module_run';

export interface InvalidityTestResourceOwner {
  resourceKind: InvalidityTestResourceKind;
  resourceId: string;
  userId: number;
  investigationId: string | null;
}

export interface InvalidityTestResourceOwnershipRepository {
  insert(owner: InvalidityTestResourceOwner): Promise<void>;
  find(
    resourceKind: InvalidityTestResourceKind,
    resourceId: string,
  ): Promise<InvalidityTestResourceOwner | null>;
}

interface OwnershipRow extends QueryResultRow {
  resource_kind: InvalidityTestResourceKind;
  resource_id: string;
  user_id: number;
  investigation_id: string | null;
}

export class InvalidityTestResourceNotFoundError extends Error {
  readonly status = 404;

  constructor() {
    super('测试资源不存在');
    this.name = 'InvalidityTestResourceNotFoundError';
  }
}

function validUserId(userId: number): boolean {
  return Number.isSafeInteger(userId) && userId > 0;
}

function validateOwner(owner: InvalidityTestResourceOwner): void {
  if (!validUserId(owner.userId) || !isInvalidityTestResourceId(owner.resourceId)) {
    throw new InvalidityTestResourceNotFoundError();
  }
  if (owner.resourceKind === 'investigation' && owner.investigationId !== null) {
    throw new Error('调查资源不能关联父调查');
  }
  if (
    owner.resourceKind === 'module_run'
    && !isInvalidityTestResourceId(owner.investigationId)
  ) {
    throw new Error('模块运行必须关联有效调查');
  }
}

export class InvalidityTestResourceOwnership {
  constructor(private readonly repository: InvalidityTestResourceOwnershipRepository) {}

  async bind(owner: InvalidityTestResourceOwner): Promise<InvalidityTestResourceOwner> {
    validateOwner(owner);
    await this.repository.insert(owner);
    return this.require(
      owner.resourceKind,
      owner.resourceId,
      owner.userId,
      owner.investigationId,
    );
  }

  async require(
    resourceKind: InvalidityTestResourceKind,
    resourceId: string,
    userId: number,
    expectedInvestigationId?: string | null,
  ): Promise<InvalidityTestResourceOwner> {
    if (!validUserId(userId) || !isInvalidityTestResourceId(resourceId)) {
      throw new InvalidityTestResourceNotFoundError();
    }
    const owner = await this.repository.find(resourceKind, resourceId);
    if (
      !owner
      || owner.userId !== userId
      || (
        expectedInvestigationId !== undefined
        && owner.investigationId !== expectedInvestigationId
      )
    ) {
      throw new InvalidityTestResourceNotFoundError();
    }
    return owner;
  }

  async requireModuleRunWithInvestigation(
    moduleRunId: string,
    userId: number,
  ): Promise<InvalidityTestResourceOwner> {
    const moduleRunOwner = await this.require('module_run', moduleRunId, userId);
    const investigationId = moduleRunOwner.investigationId;
    if (!isInvalidityTestResourceId(investigationId)) {
      throw new InvalidityTestResourceNotFoundError();
    }
    await this.require('investigation', investigationId, userId, null);
    return moduleRunOwner;
  }
}

class PostgresInvalidityTestResourceOwnershipRepository
implements InvalidityTestResourceOwnershipRepository {
  async insert(owner: InvalidityTestResourceOwner): Promise<void> {
    await ensureDatabaseReady();
    await pgQuery(
      `
        insert into invalidity_test_resource_owners (
          resource_kind,
          resource_id,
          user_id,
          investigation_id
        ) values ($1, $2, $3, $4)
        on conflict (resource_kind, resource_id) do nothing
      `,
      [owner.resourceKind, owner.resourceId, owner.userId, owner.investigationId],
    );
  }

  async find(
    resourceKind: InvalidityTestResourceKind,
    resourceId: string,
  ): Promise<InvalidityTestResourceOwner | null> {
    await ensureDatabaseReady();
    const result = await pgQuery<OwnershipRow>(
      `
        select resource_kind, resource_id, user_id, investigation_id
        from invalidity_test_resource_owners
        where resource_kind = $1 and resource_id = $2
        limit 1
      `,
      [resourceKind, resourceId],
    );
    const row = result.rows[0];
    if (!row) return null;
    return {
      resourceKind: row.resource_kind,
      resourceId: row.resource_id,
      userId: row.user_id,
      investigationId: row.investigation_id,
    };
  }
}

let defaultOwnership: InvalidityTestResourceOwnership | null = null;

export function invalidityTestResourceOwnership(): InvalidityTestResourceOwnership {
  if (!defaultOwnership) {
    defaultOwnership = new InvalidityTestResourceOwnership(
      new PostgresInvalidityTestResourceOwnershipRepository(),
    );
  }
  return defaultOwnership;
}
