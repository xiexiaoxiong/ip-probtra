import { randomUUID } from 'crypto';
import type { QueryResultRow } from 'pg';
import type { AuthUser } from './types';
import type { PatentAgentIntent } from './patent-agent';
import { ensureDatabaseReady } from './db-init';
import { pgQuery } from './postgres';

export type PatentAgentAttachment = {
  fileName: string;
  fileSize: number;
  mimeType: string;
};

export type PatentAgentMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  intent: PatentAgentIntent | null;
  attachment: PatentAgentAttachment | null;
  createdAt: string;
};

export type PatentAgentToolRun = {
  id: string;
  userMessageId: string;
  toolKind: 'infringement' | 'invalidity';
  toolVersion: string;
  status: string;
  analysisSessionId: string | null;
  investigationId: string | null;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
};

export type PatentAgentConversation = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: PatentAgentMessage[];
  toolRuns: PatentAgentToolRun[];
};

interface ConversationRow extends QueryResultRow {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface MessageRow extends QueryResultRow {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  intent: PatentAgentIntent | null;
  attachment: PatentAgentAttachment | null;
  created_at: string;
}

interface ToolRunRow extends QueryResultRow {
  id: string;
  user_message_id: string;
  tool_kind: 'infringement' | 'invalidity';
  tool_version: string;
  status: string;
  analysis_session_id: string | null;
  investigation_id: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  session_status: string | null;
}

export async function createPatentAgentConversation(
  user: AuthUser,
  firstMessage: string,
): Promise<string> {
  await ensureDatabaseReady();
  const id = randomUUID();
  const title = firstMessage.trim().replace(/\s+/g, ' ').slice(0, 42) || '新对话';
  await pgQuery(
    `insert into patent_agent_conversations (id, user_id, title) values ($1, $2, $3)`,
    [id, user.id, title],
  );
  return id;
}

export async function assertPatentAgentConversationOwner(
  conversationId: string,
  user: AuthUser,
): Promise<boolean> {
  await ensureDatabaseReady();
  const result = await pgQuery(
    `select 1 from patent_agent_conversations where id = $1 and user_id = $2 limit 1`,
    [conversationId, user.id],
  );
  return Boolean(result.rowCount);
}

export async function addPatentAgentMessage(input: {
  conversationId: string;
  user: AuthUser;
  role: 'user' | 'assistant';
  content: string;
  intent?: PatentAgentIntent | null;
  attachment?: PatentAgentAttachment | null;
}): Promise<string> {
  await ensureDatabaseReady();
  const id = randomUUID();
  await pgQuery(
    `
      insert into patent_agent_messages
        (id, conversation_id, user_id, role, content, intent, attachment)
      values ($1, $2, $3, $4, $5, $6, $7::jsonb)
    `,
    [
      id,
      input.conversationId,
      input.user.id,
      input.role,
      input.content,
      input.intent || null,
      input.attachment ? JSON.stringify(input.attachment) : null,
    ],
  );
  await pgQuery(
    `update patent_agent_conversations set updated_at = now() where id = $1 and user_id = $2`,
    [input.conversationId, input.user.id],
  );
  return id;
}

export async function createPatentAgentToolRun(input: {
  conversationId: string;
  userMessageId: string;
  user: AuthUser;
  toolKind: 'infringement' | 'invalidity';
  toolVersion: string;
}): Promise<string> {
  await ensureDatabaseReady();
  const id = randomUUID();
  await pgQuery(
    `
      insert into patent_agent_tool_runs
        (id, conversation_id, user_message_id, user_id, tool_kind, tool_version)
      values ($1, $2, $3, $4, $5, $6)
    `,
    [id, input.conversationId, input.userMessageId, input.user.id, input.toolKind, input.toolVersion],
  );
  return id;
}

export async function updatePatentAgentToolRun(input: {
  id: string;
  user: AuthUser;
  status: string;
  analysisSessionId?: string | null;
  investigationId?: string | null;
  errorMessage?: string | null;
}): Promise<void> {
  await ensureDatabaseReady();
  await pgQuery(
    `
      update patent_agent_tool_runs
      set status = $3,
          analysis_session_id = coalesce($4, analysis_session_id),
          investigation_id = coalesce($5, investigation_id),
          error_message = $6,
          updated_at = now()
      where id = $1 and user_id = $2
    `,
    [
      input.id,
      input.user.id,
      input.status,
      input.analysisSessionId || null,
      input.investigationId || null,
      input.errorMessage || null,
    ],
  );
}

export async function getPatentAgentToolRun(
  toolRunId: string,
  user: AuthUser,
): Promise<PatentAgentToolRun | null> {
  await ensureDatabaseReady();
  const result = await pgQuery<ToolRunRow>(
    `
      select
        t.id, t.user_message_id, t.tool_kind, t.tool_version, t.status,
        t.analysis_session_id, t.investigation_id, t.error_message,
        t.created_at, t.updated_at, s.status as session_status
      from patent_agent_tool_runs t
      left join analysis_sessions s
        on s.id = t.analysis_session_id and s.user_id = t.user_id
      where t.id = $1 and t.user_id = $2
      limit 1
    `,
    [toolRunId, user.id],
  );
  const tool = result.rows[0];
  if (!tool) return null;
  return {
    id: tool.id,
    userMessageId: tool.user_message_id,
    toolKind: tool.tool_kind,
    toolVersion: tool.tool_version,
    status: tool.session_status || tool.status,
    analysisSessionId: tool.analysis_session_id,
    investigationId: tool.investigation_id,
    errorMessage: tool.error_message,
    createdAt: tool.created_at,
    updatedAt: tool.updated_at,
  };
}

export async function getPatentAgentConversation(
  conversationId: string,
  user: AuthUser,
): Promise<PatentAgentConversation | null> {
  await ensureDatabaseReady();
  const conversationResult = await pgQuery<ConversationRow>(
    `
      select id, title, created_at, updated_at
      from patent_agent_conversations
      where id = $1 and user_id = $2
      limit 1
    `,
    [conversationId, user.id],
  );
  const row = conversationResult.rows[0];
  if (!row) return null;
  const [messageResult, toolResult] = await Promise.all([
    pgQuery<MessageRow>(
      `
        select id, role, content, intent, attachment, created_at
        from patent_agent_messages
        where conversation_id = $1 and user_id = $2
        order by created_at asc, id asc
      `,
      [conversationId, user.id],
    ),
    pgQuery<ToolRunRow>(
      `
        select
          t.id, t.user_message_id, t.tool_kind, t.tool_version, t.status,
          t.analysis_session_id, t.investigation_id, t.error_message,
          t.created_at, t.updated_at, s.status as session_status
        from patent_agent_tool_runs t
        left join analysis_sessions s on s.id = t.analysis_session_id and s.user_id = t.user_id
        where t.conversation_id = $1 and t.user_id = $2
        order by t.created_at asc, t.id asc
      `,
      [conversationId, user.id],
    ),
  ]);
  return {
    id: row.id,
    title: row.title,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    messages: messageResult.rows.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      intent: message.intent,
      attachment: message.attachment,
      createdAt: message.created_at,
    })),
    toolRuns: toolResult.rows.map((tool) => ({
      id: tool.id,
      userMessageId: tool.user_message_id,
      toolKind: tool.tool_kind,
      toolVersion: tool.tool_version,
      status: tool.session_status || tool.status,
      analysisSessionId: tool.analysis_session_id,
      investigationId: tool.investigation_id,
      errorMessage: tool.error_message,
      createdAt: tool.created_at,
      updatedAt: tool.updated_at,
    })),
  };
}

export async function listPatentAgentConversations(user: AuthUser): Promise<Array<{
  id: string;
  title: string;
  updatedAt: string;
  lastMessage: string | null;
}>> {
  await ensureDatabaseReady();
  const result = await pgQuery<ConversationRow & { last_message: string | null }>(
    `
      select c.id, c.title, c.created_at, c.updated_at,
        (
          select m.content from patent_agent_messages m
          where m.conversation_id = c.id
          order by m.created_at desc, m.id desc limit 1
        ) as last_message
      from patent_agent_conversations c
      where c.user_id = $1
      order by c.updated_at desc
      limit 50
    `,
    [user.id],
  );
  return result.rows.map((row) => ({
    id: row.id,
    title: row.title,
    updatedAt: row.updated_at,
    lastMessage: row.last_message,
  }));
}
