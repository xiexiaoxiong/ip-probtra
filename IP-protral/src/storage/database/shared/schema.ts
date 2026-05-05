import {
  foreignKey,
  index,
  integer,
  jsonb,
  pgTable,
  serial,
  text,
  timestamp,
  unique,
} from 'drizzle-orm/pg-core';

export const users = pgTable(
  'users',
  {
    id: serial('id').primaryKey(),
    name: text('name').notNull(),
    email: text('email').notNull(),
    passwordHash: text('password_hash').notNull(),
    role: text('role').default('user').notNull(),
    status: text('status').default('pending').notNull(),
    approvedBy: integer('approved_by'),
    approvedAt: timestamp('approved_at', { withTimezone: true, mode: 'string' }),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
  },
  (table) => [
    unique('users_email_key').on(table.email),
    index('idx_users_status').on(table.status),
    foreignKey({
      columns: [table.approvedBy],
      foreignColumns: [table.id],
      name: 'users_approved_by_fkey',
    }).onDelete('set null'),
  ],
);

export const authSessions = pgTable(
  'auth_sessions',
  {
    id: text('id').primaryKey().notNull(),
    userId: integer('user_id').notNull(),
    expiresAt: timestamp('expires_at', { withTimezone: true, mode: 'string' }).notNull(),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
    lastSeenAt: timestamp('last_seen_at', { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
  },
  (table) => [
    index('idx_auth_sessions_user_id').on(table.userId),
    index('idx_auth_sessions_expires_at').on(table.expiresAt),
    foreignKey({
      columns: [table.userId],
      foreignColumns: [users.id],
      name: 'auth_sessions_user_id_fkey',
    }).onDelete('cascade'),
  ],
);

export const analysisSessions = pgTable(
  'analysis_sessions',
  {
    id: text('id').primaryKey().notNull(),
    userId: integer('user_id').notNull(),
    status: text('status').default('pending').notNull(),
    inputType: text('input_type').notNull(),
    inputValue: text('input_value'),
    fileName: text('file_name'),
    fileUrl: text('file_url'),
    textContent: text('text_content'),
    patentTitle: text('patent_title'),
    patentNumber: text('patent_number'),
    results: jsonb('results').default({}).notNull(),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
  },
  (table) => [
    index('idx_analysis_sessions_created_at').on(table.createdAt),
    index('idx_analysis_sessions_status').on(table.status),
    index('idx_analysis_sessions_user_id').on(table.userId),
    foreignKey({
      columns: [table.userId],
      foreignColumns: [users.id],
      name: 'analysis_sessions_user_id_fkey',
    }).onDelete('cascade'),
  ],
);

export const analysisSteps = pgTable(
  'analysis_steps',
  {
    id: serial('id').primaryKey().notNull(),
    sessionId: text('session_id').notNull(),
    stepId: integer('step_id').notNull(),
    stepName: text('step_name').notNull(),
    status: text('status').default('pending').notNull(),
    error: text('error'),
    startedAt: timestamp('started_at', { withTimezone: true, mode: 'string' }),
    completedAt: timestamp('completed_at', { withTimezone: true, mode: 'string' }),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
  },
  (table) => [
    index('idx_analysis_steps_session_id').on(table.sessionId),
    unique('analysis_steps_session_id_step_id_key').on(table.sessionId, table.stepId),
    foreignKey({
      columns: [table.sessionId],
      foreignColumns: [analysisSessions.id],
      name: 'analysis_steps_session_id_fkey',
    }).onDelete('cascade'),
  ],
);

export const healthCheck = pgTable('health_check', {
  id: serial('id').notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'string' }).defaultNow(),
});
