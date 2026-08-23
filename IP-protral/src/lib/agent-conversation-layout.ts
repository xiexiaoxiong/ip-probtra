export type AgentConversationLayoutMessage = {
  id: string;
  role: 'user' | 'assistant';
};

/**
 * Tool runs are created from a user message, while the persisted assistant
 * acknowledgement is normally appended immediately afterwards. Anchor the
 * process card to the last assistant message in that turn so the visual order
 * remains: request -> acknowledgement -> work progress -> final result.
 */
export function agentToolAnchorMessageId(
  messages: AgentConversationLayoutMessage[],
  userMessageId: string,
): string {
  const userIndex = messages.findIndex((message) => message.id === userMessageId);
  if (userIndex < 0) return userMessageId;

  let anchorId = userMessageId;
  for (let index = userIndex + 1; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role === 'user') break;
    if (message.role === 'assistant') anchorId = message.id;
  }
  return anchorId;
}
