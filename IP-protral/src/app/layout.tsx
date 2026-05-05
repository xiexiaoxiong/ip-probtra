import type { Metadata } from 'next';
import { Inspector } from 'react-dev-inspector';
import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'IP-Probtra 专利侵权自动识别系统',
    template: '%s | IP-Probtra 专利侵权自动识别系统',
  },
  description:
    '基于专利文本与市场商品信息，进行事实驱动、可回溯、可验证的侵权技术比对，输出 Claim Chart 级别的专业分析结果。',
  keywords: [
    '专利侵权',
    '侵权分析',
    'Claim Chart',
    '权利要求比对',
    '专利检索',
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const isDev = process.env.COZE_PROJECT_ENV === 'DEV';

  return (
    <html lang="zh-CN">
      <body className={`antialiased`}>
        {isDev && <Inspector />}
        {children}
      </body>
    </html>
  );
}
