import type { AnalysisResults, ProductComparison, ProductInfo } from './types';

export type AsyncModule3TaskStatus = NonNullable<AnalysisResults['module3TaskStatus']>;

export interface Module3ProgressState {
  productsCount: number;
  isComplete: boolean;
  taskStatus?: AsyncModule3TaskStatus;
}

export function isModule3Terminal(status?: string): boolean {
  return status === 'completed'
    || status === 'error'
    || status === 'cancelled'
    || status === 'timeout';
}

export function shouldTriggerInitialModule4(params: {
  initialStep5Triggered: boolean;
  module3ProductsCount: number;
  module3IsComplete: boolean;
}): boolean {
  return !params.initialStep5Triggered
    && params.module3ProductsCount > 0
    && !params.module3IsComplete;
}

export function buildInitialModule4PartialResults(params: {
  products: ProductInfo[];
  comparisons: ProductComparison[];
  claimCompareRunId?: number;
  module4RunId?: string;
  module4TaskStatus?: AnalysisResults['module4TaskStatus'];
  module4TaskStartedAt?: string;
  module4TaskFinishedAt?: string;
}): Partial<AnalysisResults> {
  return {
    products: params.products,
    comparisons: params.comparisons,
    initialClaimCompareRunId: params.claimCompareRunId,
    claimCompareRunId: params.claimCompareRunId,
    resultsCompleteness: 'partial',
    step5Phase: 'initial',
    partialAnalysisAvailable: true,
    module4RunId: params.module4RunId,
    module4TaskStatus: params.module4TaskStatus,
    module4TaskStartedAt: params.module4TaskStartedAt,
    module4TaskFinishedAt: params.module4TaskFinishedAt,
    module4TaskError: undefined,
    module4Exception: undefined,
  };
}

export function buildFinalModule4Results(params: {
  products: ProductInfo[];
  comparisons: ProductComparison[];
  initialClaimCompareRunId?: number;
  finalClaimCompareRunId?: number;
  module4RunId?: string;
  module4TaskStatus?: AnalysisResults['module4TaskStatus'];
  module4TaskStartedAt?: string;
  module4TaskFinishedAt?: string;
}): Partial<AnalysisResults> {
  return {
    products: params.products,
    comparisons: params.comparisons,
    claimCompareRunId: params.finalClaimCompareRunId,
    initialClaimCompareRunId: params.initialClaimCompareRunId,
    finalClaimCompareRunId: params.finalClaimCompareRunId,
    resultsCompleteness: 'final',
    step5Phase: 'completed',
    partialAnalysisAvailable: Boolean(params.initialClaimCompareRunId),
    module4RunId: params.module4RunId,
    module4TaskStatus: params.module4TaskStatus,
    module4TaskStartedAt: params.module4TaskStartedAt,
    module4TaskFinishedAt: params.module4TaskFinishedAt,
    module4TaskError: undefined,
    module4Exception: undefined,
  };
}
