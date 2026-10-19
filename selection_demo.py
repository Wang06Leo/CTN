"""Show local selection ranking for a synthetic fixture (no network access)."""
from pathlib import Path
from ctn.replay_client import ReplaySocialClient
from ctn.selection import select_longest_ascii_question


def main():
    posts = ReplaySocialClient.from_file(Path('fixtures/length_priority_demo.json')).get_feed()
    print('ASCII ? candidates (descending by len(text); stable feed-order ties):')
    candidates = [(order, post) for order, post in enumerate(posts) if '?' in post.text]
    for _, post in sorted(candidates, key=lambda pair: -len(pair[1].text)):
        print(f'  {post.id:<24} {len(post.text):>3} chars')
    selected = select_longest_ascii_question(posts)
    print('Selected post ID:', selected.id if selected else 'None')
    print('All content and IDs are synthetic offline data.')


if __name__ == '__main__':
    main()
