import { redirect } from 'next/navigation';

/**
 * 旧的独立无效分析入口已经并入首页专利 Agent。
 * 保留这个兼容路由，避免历史书签落到 404；真正的结果页仍在 /invalidity/results。
 */
export default function InvalidityLegacyPage() {
  redirect('/');
}
