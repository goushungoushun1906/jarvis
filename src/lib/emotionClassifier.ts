export type EmotionType = 'happy' | 'focused' | 'confused' | 'surprised' | 'neutral';

export interface EmotionResult {
  emotion: EmotionType;
  intensity: number; // 0-1
}

const emotionKeywords: Record<EmotionType, string[]> = {
  happy: [
    // Chinese
    '好的', '当然', '没问题', '很高兴', '乐意', '哈哈', '太好了', '不错', '成功',
    '完成', '完美', '优秀', '棒', '赞', '喜欢', '开心', '高兴', '感谢', '谢谢',
    '恭喜', '出色', '精彩', '满意', '顺利', '已经准备好', '很乐意',
    // English
    'great', 'wonderful', 'excellent', 'perfect', 'happy', 'glad', 'awesome',
    'good job', 'well done', 'congratulations', 'love', 'enjoy', 'success',
    'amazing', 'fantastic', 'brilliant', 'nice', 'thank you', 'cheers',
  ],
  focused: [
    // Chinese
    '分析', '计算', '查询', '搜索', '处理', '步骤', '方法', '数据', '结果',
    '报告', '根据', '首先', '然后', '接下来', '结论', '因此', '综上', '具体来说',
    '详细', '方案', '执行', '流程', '总结', '整理', '分类', '对比',
    // English
    'analyze', 'calculate', 'process', 'result', 'report', 'according', 'first',
    'then', 'next', 'conclusion', 'therefore', 'specifically', 'detail', 'plan',
    'execute', 'workflow', 'summarize', 'compare', 'data', 'method', 'approach',
  ],
  confused: [
    // Chinese
    '不确定', '不清楚', '可能', '也许', '大概', '似乎', '不太', '没有找到',
    '无法确定', '我不确定', '猜测', '大概', '可能需要', '需要确认',
    '有些模糊', '我不太了解', '没有足够信息', '这取决于', '需要进一步',
    // English
    'uncertain', 'not sure', 'unclear', 'maybe', 'perhaps', 'probably', 'seems',
    'might', 'could be', 'not found', 'guess', 'depends', 'need more info',
    'ambiguous', 'unclear', 'i do not know',
  ],
  surprised: [
    // Chinese
    '哇', '竟然', '居然', '没想到', '惊喜', '太棒了', '厉害', '意外',
    '出乎意料', '不可思议', '难以置信', '令人惊讶', '没想到',
    // English
    'wow', 'surprising', 'unexpected', 'incredible', 'unbelievable', 'amazing',
    'astonishing', 'remarkable', 'who would have thought', 'did not expect',
  ],
  neutral: [],
};

/**
 * Rule-based emotion classifier that analyzes AI response text.
 * Counts keyword matches per emotion category and returns the
 * highest-scoring emotion with an intensity score.
 */
export function classifyEmotion(text: string): EmotionResult {
  const lowerText = text.toLowerCase();

  const scores: Record<EmotionType, number> = {
    happy: 0,
    focused: 0,
    confused: 0,
    surprised: 0,
    neutral: 0,
  };

  let totalMatches = 0;

  for (const [emotion, keywords] of Object.entries(emotionKeywords)) {
    if (emotion === 'neutral') continue;

    for (const keyword of keywords) {
      const lower = keyword.toLowerCase();
      // Count occurrences of this keyword in the text
      let idx = 0;
      let count = 0;
      while ((idx = lowerText.indexOf(lower, idx)) !== -1) {
        count++;
        idx += lower.length;
        // Cap per-keyword contribution to avoid gaming by repetition
        if (count >= 3) break;
      }
      if (count > 0) {
        scores[emotion as EmotionType] += count;
        totalMatches += count;
      }
    }
  }

  // Find highest-scoring emotion
  let bestEmotion: EmotionType = 'neutral';
  let bestScore = 0;

  for (const [emotion, score] of Object.entries(scores)) {
    if (emotion === 'neutral') continue;
    if (score > bestScore) {
      bestScore = score;
      bestEmotion = emotion as EmotionType;
    }
  }

  // Intensity: ratio of best emotion matches to total matches, normalized
  // Also factor in text length for base intensity
  const matchIntensity = totalMatches > 0 ? bestScore / totalMatches : 0;
  const textLengthFactor = Math.min(text.length / 200, 1); // longer text = more confident
  const intensity = Math.min(matchIntensity * 0.7 + textLengthFactor * 0.3, 1);

  // If no significant emotion detected, default to neutral
  if (bestScore < 1) {
    return { emotion: 'neutral', intensity: 0.2 };
  }

  return {
    emotion: bestEmotion,
    intensity: Math.max(intensity, 0.3), // minimum intensity if we found something
  };
}
