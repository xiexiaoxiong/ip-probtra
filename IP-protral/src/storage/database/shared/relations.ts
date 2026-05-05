import { relations } from 'drizzle-orm/relations';
import { analysisSessions, analysisSteps, authSessions, users } from './schema';

export const usersRelations = relations(users, ({ many, one }) => ({
  analysisSessions: many(analysisSessions),
  authSessions: many(authSessions),
  approvedUsers: many(users, { relationName: 'user_approver' }),
  approver: one(users, {
    fields: [users.approvedBy],
    references: [users.id],
    relationName: 'user_approver',
  }),
}));

export const authSessionsRelations = relations(authSessions, ({ one }) => ({
  user: one(users, {
    fields: [authSessions.userId],
    references: [users.id],
  }),
}));

export const analysisStepsRelations = relations(analysisSteps, ({ one }) => ({
  analysisSession: one(analysisSessions, {
    fields: [analysisSteps.sessionId],
    references: [analysisSessions.id],
  }),
}));

export const analysisSessionsRelations = relations(analysisSessions, ({ many, one }) => ({
  analysisSteps: many(analysisSteps),
  user: one(users, {
    fields: [analysisSessions.userId],
    references: [users.id],
  }),
}));
