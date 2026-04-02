/**
 * Utility functions for knowledge base file operations.
 *
 * These helpers demonstrate how file-based search can be implemented
 * in TypeScript as an alternative to vector search.
 */

import { readdir, readFile, stat } from "fs/promises";
import { join, relative } from "path";

/** Recursively list all files under a directory. */
export async function listAllFiles(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true });
  const files: string[] = [];

  for (const entry of entries) {
    const fullPath = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listAllFiles(fullPath)));
    } else {
      files.push(fullPath);
    }
  }

  return files;
}

/** Simple keyword search across all files in a directory. */
export async function keywordSearch(
  dir: string,
  keyword: string
): Promise<{ file: string; line: number; content: string }[]> {
  const files = await listAllFiles(dir);
  const results: { file: string; line: number; content: string }[] = [];

  for (const filePath of files) {
    const content = await readFile(filePath, "utf-8");
    const lines = content.split("\n");

    for (let i = 0; i < lines.length; i++) {
      if (lines[i].toLowerCase().includes(keyword.toLowerCase())) {
        results.push({
          file: relative(dir, filePath),
          line: i + 1,
          content: lines[i].trim(),
        });
      }
    }
  }

  return results;
}

/** Get file metadata for knowledge base overview. */
export async function getFileStats(filePath: string) {
  const s = await stat(filePath);
  return {
    size: s.size,
    modified: s.mtime.toISOString(),
    isDirectory: s.isDirectory(),
  };
}
