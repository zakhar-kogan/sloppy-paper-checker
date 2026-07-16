export function isResolvableInput(value: string): boolean {
  const input = value.trim();
  return /^(https?:\/\/\S+|10\.\d{4,9}\/\S+|pmc\d+|pmid:\s*\d+|arxiv:\s*\S+|\d{4}\.\d{4,5}(v\d+)?|\d{5,9})$/i.test(input);
}

export function duration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${remainder.toString().padStart(2, "0")}s` : `${remainder}s`;
}
