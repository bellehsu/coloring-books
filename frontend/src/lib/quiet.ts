/** 失敗已經另外提示過（toast）的 promise：吞掉 rejection，呼叫端不用再接。 */
export const quiet = (p: Promise<unknown>) => p.catch(() => {});
